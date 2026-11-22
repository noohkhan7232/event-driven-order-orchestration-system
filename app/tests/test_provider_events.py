from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_consumers import consume

from app.domain.order_state import transition
from app.events.contracts import DomainEvent, EventNames
from app.models.entities import OutboxEvent, Payment
from app.models.enums import OrderStatus, PaymentStatus
from app.providers.contracts import ProviderEvent
from app.services.inbound_payments import stage_receipt
from app.workers import tasks


@pytest.fixture
def pending_payment(db, workflow):
    order, stock = workflow
    order.total_amount, order.currency = Decimal("10.00"), "INR"
    db.commit()
    consume(order, EventNames.ORDER_CREATED)
    transition(order, OrderStatus.PAYMENT_PENDING, db=db)
    order.payment_expires_at = datetime.now(UTC) + timedelta(minutes=30)
    payment = Payment(
        order_id=order.id,
        provider="razorpay",
        provider_reference="order_provider",
        amount=order.total_amount,
        status=PaymentStatus.INITIATED,
        provider_status="pending",
        idempotency_key=f"{order.id}:payment:1",
    )
    db.add(payment)
    db.commit()
    return order, payment, stock


def deliver(
    db,
    order,
    *,
    event_id=None,
    payment_id="pay_provider",
    kind="payment_captured",
    amount=1000,
    currency="INR",
    provider="razorpay",
    order_ref="order_provider",
    refund_id=None,
):
    notice = ProviderEvent(
        event_id or "evt_" + uuid4().hex, kind, order_ref, payment_id, refund_id, amount, currency, {}
    )
    identity = stage_receipt(db, provider, order.tenant_id, notice.event_id, [notice])
    db.commit()
    row = db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_id == identity, OutboxEvent.event_type == "payment.provider_event"
        )
    )
    tasks.process_event.run(DomainEvent.model_validate(row.payload).model_dump(mode="json"))
    return identity


def test_capture_and_order_paid_equivalent_have_one_financial_effect(db, pending_payment):
    from app.models.entities import InboundWebhookEvent, OrderTimeline, Refund

    order, payment, _ = pending_payment
    deliver(db, order, event_id="evt_first")
    assert order.status == OrderStatus.PAID and payment.status == PaymentStatus.AUTHORIZED
    assert payment.provider_transaction_id == "pay_provider"
    deliver(db, order, event_id="evt_second")
    assert db.query(Payment).count() == 1 and db.query(Refund).count() == 0
    assert db.query(InboundWebhookEvent).filter_by(status="processed").count() == 2
    assert db.query(OrderTimeline).filter_by(to_status="paid").count() == 1
    assert db.query(OutboxEvent).filter_by(event_type=EventNames.PAYMENT_AUTHORIZED).count() == 1
    deliver(db, order, event_id="evt_first")
    assert db.query(InboundWebhookEvent).count() == 2


@pytest.mark.parametrize("kind", ["amount", "currency", "cancelled", "expired", "duplicate"])
def test_invalid_or_extra_capture_creates_one_durable_refund_without_fulfillment(db, pending_payment, kind):
    from app.models.entities import AuditLog, Refund

    order, payment, stock = pending_payment
    if kind == "cancelled":
        transition(order, OrderStatus.CANCELLED, db=db)
    if kind == "expired":
        order.payment_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    if kind == "duplicate":
        deliver(db, order)
    db.commit()
    identity = "pay_extra" if kind == "duplicate" else "pay_provider"
    before = order.status
    deliver(
        db,
        order,
        payment_id=identity,
        amount=2000 if kind == "amount" else 1000,
        currency="USD" if kind == "currency" else "INR",
    )
    refund = db.query(Refund).one()
    expected = (
        "AMOUNT_MISMATCH"
        if kind in {"amount", "currency"}
        else "DUPLICATE_PAYMENT"
        if kind == "duplicate"
        else "LATE_PAYMENT"
    )
    assert refund.reason == expected and refund.status == "requested"
    assert refund.amount == (Decimal("20.00") if kind == "amount" else Decimal("10.00"))
    assert refund.currency == ("USD" if kind == "currency" else "INR")
    assert db.get(Payment, refund.payment_id).provider_transaction_id == identity
    assert order.status == before and stock.available_quantity == 9
    assert db.query(AuditLog).filter_by(action=expected).count() == 1
    deliver(
        db,
        order,
        payment_id=identity,
        amount=2000 if kind == "amount" else 1000,
        currency="USD" if kind == "currency" else "INR",
    )
    assert db.query(Refund).count() == 1
    assert db.query(OutboxEvent).filter_by(event_type="refund.requested").count() == 1


def test_payment_failed_is_nonterminal_and_later_capture_succeeds(db, pending_payment):
    order, payment, stock = pending_payment
    deliver(db, order, kind="payment_failed")
    assert order.status == OrderStatus.PAYMENT_PENDING and payment.status == PaymentStatus.INITIATED
    assert payment.provider_status == "failed" and stock.available_quantity == 9
    deliver(db, order)
    assert order.status == OrderStatus.PAID and payment.provider_status == "captured"


def test_refunded_mismatched_charge_does_not_replace_primary_payment_summary(db, pending_payment):
    from app.models.entities import Refund

    order, payment, _ = pending_payment
    deliver(db, order, amount=2000, payment_id="pay_wrong")
    refund = db.query(Refund).one()
    deliver(db, order, kind="refund_processed", amount=2000, payment_id="pay_wrong", refund_id="rfnd_wrong")
    assert refund.status == "succeeded"
    assert order.payment_status == "pending" and order.checkout is None
    assert order.primary_payment.id == payment.id
    deliver(db, order, payment_id="pay_correct")
    assert order.status == OrderStatus.PAID and order.payment_status == "captured"
    assert order.primary_payment.id == payment.id


def test_refund_notifications_preserve_success_and_do_not_duplicate_effects(db, pending_payment):
    from app.models.entities import AuditLog, Refund
    from app.services.refund_intents import request_refund, succeed_refund

    order, _, _ = pending_payment
    deliver(db, order, amount=2000)
    refund = db.query(Refund).one()
    charge = db.get(Payment, refund.payment_id)
    assert request_refund(db, order, charge, "AMOUNT_MISMATCH").id == refund.id
    db.commit()
    deliver(db, order, kind="refund_processed", amount=2000, currency="USD", refund_id="rfnd_one")
    assert refund.status == "requested"
    deliver(db, order, kind="refund_failed", amount=2000, refund_id="rfnd_one")
    assert refund.status == "failed" and charge.status == PaymentStatus.CAPTURED
    deliver(db, order, kind="refund_processed", amount=2000, refund_id="rfnd_one")
    assert refund.status == "succeeded" and charge.status == PaymentStatus.REFUNDED
    deliver(db, order, kind="refund_processed", amount=2000, refund_id="rfnd_one")
    deliver(db, order, kind="refund_failed", amount=2000, refund_id="rfnd_one")
    succeed_refund(db, refund, charge, "rfnd_one")
    assert refund.status == "succeeded"
    assert db.query(OutboxEvent).filter_by(event_type="refund.succeeded").count() == 1
    assert db.query(AuditLog).filter_by(action="payment_refunded").count() == 1
    assert request_refund(db, order, Payment(provider="cod"), "ORDER_CANCELLED") is None


def test_provider_event_rollback_is_retryable_without_partial_financial_effect(db, pending_payment, monkeypatch):
    from app.core.exceptions import TransientError
    from app.models.entities import InboundWebhookEvent, OrderTimeline, ProcessedEvent
    from app.repositories.outbox import OutboxRepository

    order, payment, _ = pending_payment
    original = OutboxRepository.add

    def broken(repo, event, queue):
        if event.event_type == EventNames.PAYMENT_AUTHORIZED:
            raise RuntimeError("synthetic failure")
        return original(repo, event, queue)

    monkeypatch.setattr(OutboxRepository, "add", broken)
    with pytest.raises(TransientError):
        deliver(db, order, event_id="evt_rollback")
    db.expire_all()
    assert order.status == OrderStatus.PAYMENT_PENDING and payment.provider_transaction_id is None
    assert db.query(InboundWebhookEvent).one().status == "received"
    assert db.query(ProcessedEvent).filter_by(consumer="payment_provider").count() == 0
    assert db.query(OrderTimeline).filter_by(to_status="paid").count() == 0
    monkeypatch.setattr(OutboxRepository, "add", original)
    deliver(db, order, event_id="evt_rollback")
    assert order.status == OrderStatus.PAID


def test_unknown_and_foreign_provider_references_have_no_effect(db, pending_payment):
    from app.core.exceptions import TransientError
    from app.models.entities import Order, Tenant

    order, _, _ = pending_payment
    tenant = Tenant(name="Foreign", slug="foreign-provider-event")
    db.add(tenant)
    db.flush()
    foreign = Order(
        tenant_id=tenant.id,
        principal_scope="tenant",
        idempotency_key="foreign",
        status=OrderStatus.PAYMENT_PENDING,
        total_amount=10,
        currency="INR",
    )
    db.add(foreign)
    db.flush()
    charge = Payment(
        order_id=foreign.id,
        provider="razorpay",
        provider_reference="order_foreign",
        amount=10,
        status=PaymentStatus.INITIATED,
    )
    db.add(charge)
    db.commit()
    with pytest.raises(TransientError):
        deliver(db, order, order_ref="order_foreign")
    assert foreign.status == order.status == OrderStatus.PAYMENT_PENDING
    assert charge.provider_transaction_id is None
    event = DomainEvent(
        event_type="payment.provider_event", aggregate_id=uuid4(), payload={}, tenant_id=order.tenant_id
    )
    with pytest.raises(TransientError):
        tasks.process_event.run(event.model_dump(mode="json"))


def test_ignored_notification_and_unmatched_refund_are_safe(db, pending_payment):
    from app.core.exceptions import TransientError
    from app.models.entities import InboundWebhookEvent

    order, payment, _ = pending_payment
    identity = stage_receipt(db, "razorpay", order.tenant_id, "evt_ignored", [])
    db.commit()
    queued = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == identity))
    tasks.process_event.run(queued.payload)
    tasks.process_event.run(queued.payload)
    assert db.get(InboundWebhookEvent, identity).status == "ignored"
    deliver(db, order)
    with pytest.raises(TransientError):
        deliver(db, order, kind="refund_processed", refund_id="rfnd_unknown")
    assert payment.status == PaymentStatus.AUTHORIZED
    deliver(db, order, amount=2000, payment_id="pay_extra")
    with pytest.raises(TransientError):
        deliver(db, order, kind="refund_processed", amount=2000, payment_id="pay_extra")


@pytest.mark.postgres
@pytest.mark.parametrize("same_receipt", [True, False])
def test_postgres_concurrent_capture_notifications_have_one_effect(db, pending_payment, monkeypatch, same_receipt):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from conftest import TestingSessionLocal

    from app.models.entities import InboundWebhookEvent, Refund
    from app.repositories.orders import OrderRepository
    from app.services.provider_events import process_provider_event

    order, _, _ = pending_payment
    event_ids = ["evt_concurrent_one"] * 2 if same_receipt else ["evt_concurrent_one", "evt_concurrent_two"]
    queued = []
    for event_id in event_ids:
        notice = ProviderEvent(event_id, "payment_captured", "order_provider", "pay_provider", None, 1000, "INR", {})
        identity = stage_receipt(db, "razorpay", order.tenant_id, event_id, [notice])
        db.commit()
        queued.append(
            DomainEvent.model_validate(
                db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == identity)).payload
            )
        )
    db.commit()
    gate = Barrier(2)
    original = OrderRepository.get_with_details

    def synchronize(repo, *args, **kwargs):
        gate.wait(timeout=15)
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(OrderRepository, "get_with_details", synchronize)

    def consume_in_session(event):
        with TestingSessionLocal() as session:
            process_provider_event(session, event)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(consume_in_session, queued))
    db.expire_all()
    assert order.status == OrderStatus.PAID
    assert db.query(InboundWebhookEvent).filter_by(status="processed").count() == (1 if same_receipt else 2)
    assert db.query(OutboxEvent).filter_by(event_type=EventNames.PAYMENT_AUTHORIZED).count() == 1
    assert db.query(Payment).count() == 1 and db.query(Refund).count() == 0
    print("CONCURRENT CAPTURES: ONE PAID TRANSITION AND ONE FULFILLMENT INTENT")
