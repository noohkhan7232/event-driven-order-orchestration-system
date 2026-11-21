from unittest.mock import patch

import pytest
from test_consumers import consume

from app.core.config import get_settings
from app.core.exceptions import TransientError
from app.events.contracts import DomainEvent, EventNames
from app.models.entities import Payment, ProcessedEvent
from app.models.enums import OrderStatus, PaymentStatus
from app.schemas.orders import OrderCreate, OrderItemCreate, OrderRead
from app.services.orders import OrderService


def test_default_payment_hash_remains_legacy_compatible():
    payload = OrderCreate(items=[OrderItemCreate(sku="SKU", quantity=1)])
    assert OrderService._request_hash(payload, "INR") == OrderService._request_hash(
        payload.model_copy(update={"payment_method": "prepaid"}), "INR"
    )
    assert OrderService._request_hash(payload, "INR") != OrderService._request_hash(
        payload.model_copy(update={"payment_method": "cod"}), "INR"
    )


def test_capture_pipeline_checks_state_reference_type_and_exact_money(db, workflow):
    from dataclasses import replace
    from decimal import Decimal

    from app.providers.contracts import ProviderEvent
    from app.services.payment_events import apply_capture

    order, _ = workflow
    payment = Payment(amount=Decimal("10"), provider_reference="order_safe")
    notice = ProviderEvent("evt_safe", "payment_captured", "order_safe", "pay_safe", None, 1000, "USD", {})
    event = DomainEvent(event_type="payment.requested", aggregate_id=order.id, payload={})
    apply_capture(db, order, payment, notice, event)
    order.status = OrderStatus.PAYMENT_PENDING
    apply_capture(db, order, payment, replace(notice, provider_order_id="different"), event)
    apply_capture(db, order, payment, replace(notice, type="payment_failed"), event)
    with pytest.raises(TransientError, match="amount mismatch"):
        apply_capture(db, order, payment, replace(notice, amount_minor=999), event)
    db.rollback()


def test_cod_shipment_failure_compensates_without_refund(db, workflow):
    from app.providers.contracts import ProviderDeclined
    from app.services.shipping import ShippingService

    order, stock = workflow
    order.payment_method = "cod"
    db.commit()
    consume(order, EventNames.ORDER_CREATED)
    with patch.object(ShippingService, "create_shipment", side_effect=ProviderDeclined("synthetic")):
        consume(order, "payment.requested")
    assert order.status == OrderStatus.FAILED and stock.available_quantity == 10
    assert db.query(Payment).one().status == PaymentStatus.FAILED


def test_legacy_pending_order_without_session_is_a_safe_noop(db, workflow):
    order, _ = workflow
    order.status = OrderStatus.PAYMENT_PENDING
    db.commit()
    consume(order, "payment.requested")
    assert db.query(Payment).count() == 0 and order.status == OrderStatus.PAYMENT_PENDING


def test_unconfigured_prepaid_rejected_and_cod_still_allowed(db, workflow):
    from app.models.entities import User
    from app.providers.resolution import PaymentNotConfigured

    order, _ = workflow
    user = db.get(User, order.customer_id)
    payload = OrderCreate(items=[OrderItemCreate(sku="SKU", quantity=1)])
    with patch.object(get_settings(), "allow_mock_providers", False):
        with pytest.raises(PaymentNotConfigured):
            OrderService(db).create_order(user, payload, "unconfigured")
        created = OrderService(db).create_order(
            user, payload.model_copy(update={"payment_method": "cod"}), "unconfigured"
        )
    assert created.payment_method == "cod"


def test_cod_cancellation_cancels_shipment_and_never_refunds(db, workflow):
    from app.models.entities import AuditLog, Shipment, User

    order, stock = workflow
    order.payment_method = "cod"
    db.commit()
    consume(order, EventNames.ORDER_CREATED)
    consume(order, "payment.requested")
    OrderService(db).cancel_order(order.id, db.get(User, order.customer_id))
    assert order.status == OrderStatus.CANCELLED and stock.available_quantity == 10
    assert db.query(Shipment).one().status.value == "cancelled"
    assert db.query(Payment).one().status == PaymentStatus.INITIATED
    assert db.query(AuditLog).filter_by(action="payment_refunded").count() == 0


def test_session_pending_checkout_and_replay(db, workflow):
    order, stock = workflow
    consume(order, EventNames.ORDER_CREATED)
    with patch(
        "app.services.payment_sessions.get_settings",
        return_value=get_settings().model_copy(
            update={"allow_mock_providers": True, "mock_payment_auto_complete": False}
        ),
    ):
        event = consume(order, "payment.requested")
    assert order.status == OrderStatus.PAYMENT_PENDING
    payment = db.query(Payment).one()
    assert payment.idempotency_key == f"{order.id}:payment:1"
    assert payment.status == PaymentStatus.INITIATED and payment.provider_reference
    body = OrderRead.model_validate(order).model_dump(mode="json")
    assert body["payment_method"] == "prepaid" and body["payment_status"] == "pending"
    assert set(body["checkout"]) == {
        "provider",
        "provider_order_id",
        "amount_minor",
        "currency",
        "key_id",
        "expires_at",
    }
    consume(order, event.event_type, event)
    assert db.query(Payment).count() == 1 and stock.available_quantity == 9


def test_provider_call_happens_after_commit_and_retry_resumes_same_payment(db, workflow):
    from app.providers.contracts import ProviderUnavailable
    from app.providers.mock_v2 import MockPaymentProviderV2

    order, _ = workflow
    consume(order, EventNames.ORDER_CREATED)
    original = MockPaymentProviderV2.create_payment_session
    payment_ids = []

    def unavailable(provider, snapshot, payment, key):
        assert not db.in_transaction()
        payment_ids.append(payment.id)
        raise ProviderUnavailable("synthetic failure")

    event = DomainEvent(event_type="payment.requested", aggregate_id=order.id, payload={})
    with patch.object(MockPaymentProviderV2, "create_payment_session", unavailable):
        with pytest.raises(TransientError):
            consume(order, event.event_type, event)
    assert db.query(Payment).one().id == payment_ids[0]
    assert order.status == OrderStatus.PAYMENT_PENDING
    assert db.query(ProcessedEvent).filter_by(event_id=event.event_id).count() == 0
    with patch.object(MockPaymentProviderV2, "create_payment_session", original):
        consume(order, event.event_type, event)
    assert db.query(Payment).one().id == payment_ids[0] and order.status == OrderStatus.PAID


def test_cod_has_no_session_and_collects_on_delivery(db, workflow):
    from app.domain.order_state import transition

    order, _ = workflow
    order.payment_method = "cod"
    db.commit()
    consume(order, EventNames.ORDER_CREATED)
    consume(order, "payment.requested")
    assert order.status == OrderStatus.FULFILLMENT_REQUESTED
    payment = db.query(Payment).one()
    assert payment.provider == "cod" and payment.provider_reference is None
    transition(order, OrderStatus.SHIPPED, db=db)
    transition(order, OrderStatus.DELIVERED, db=db)
    db.commit()
    event = DomainEvent(event_type=EventNames.ORDER_STATUS_CHANGED, aggregate_id=order.id, payload={"to": "delivered"})
    consume(order, event.event_type, event)
    consume(order, event.event_type, event)
    assert payment.status == PaymentStatus.CAPTURED and payment.provider_status == "collected"


def test_cancellation_during_provider_call_discards_session_result(db, workflow):
    from app.models.entities import User
    from app.providers.mock_v2 import MockPaymentProviderV2

    order, stock = workflow
    consume(order, EventNames.ORDER_CREATED)
    original = MockPaymentProviderV2.create_payment_session

    def cancel(provider, snapshot, payment, key):
        assert not db.in_transaction()
        OrderService(db).cancel_order(snapshot.id, db.get(User, order.customer_id))
        return original(provider, snapshot, payment, key)

    with patch.object(MockPaymentProviderV2, "create_payment_session", cancel):
        consume(order, "payment.requested")
    assert order.status == OrderStatus.CANCELLED and stock.available_quantity == 10
    assert db.query(Payment).one().provider_reference is None
    assert OrderRead.model_validate(order).checkout is None


def test_crash_in_final_phase_retries_the_same_durable_attempt(db, workflow):
    from app.repositories.outbox import OutboxRepository

    order, _ = workflow
    consume(order, EventNames.ORDER_CREATED)
    original = OutboxRepository.add
    event = DomainEvent(event_type="payment.requested", aggregate_id=order.id, payload={})

    def crash(repo, notice, queue):
        if notice.event_type == "payment.provider_event":
            raise RuntimeError("synthetic crash")
        return original(repo, notice, queue)

    with patch.object(OutboxRepository, "add", crash):
        with pytest.raises(TransientError):
            consume(order, event.event_type, event)
    db.expire_all()
    row = db.query(Payment).one()
    identity, key = row.id, row.idempotency_key
    assert row.provider_reference is None and order.status == OrderStatus.PAYMENT_PENDING
    assert db.query(ProcessedEvent).filter_by(event_id=event.event_id).count() == 0
    consume(order, event.event_type, event)
    assert db.query(Payment).one().id == identity and db.query(Payment).one().idempotency_key == key
    assert order.status == OrderStatus.PAID


@pytest.mark.postgres
def test_postgres_provider_call_releases_order_and_inventory_locks(db, workflow):
    from concurrent.futures import ThreadPoolExecutor

    from conftest import TestingSessionLocal
    from sqlalchemy import select

    from app.models.entities import Inventory, Order
    from app.providers.mock_v2 import MockPaymentProviderV2

    order, stock = workflow
    consume(order, EventNames.ORDER_CREATED)
    original = MockPaymentProviderV2.create_payment_session

    def probe(order_id, stock_id):
        with TestingSessionLocal() as observer:
            assert (
                observer.scalar(select(Order.id).where(Order.id == order_id).with_for_update(nowait=True)) == order_id
            )
            assert (
                observer.scalar(select(Inventory.id).where(Inventory.id == stock_id).with_for_update(nowait=True))
                == stock_id
            )

    stock_id = stock.id
    db.rollback()

    def blocking(provider, snapshot, payment, key):
        assert not db.in_transaction()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(probe, snapshot.id, stock_id).result(timeout=10)
        return original(provider, snapshot, payment, key)

    with patch.object(MockPaymentProviderV2, "create_payment_session", blocking):
        consume(order, "payment.requested")
    print("PROVIDER IN FLIGHT: CONCURRENT ORDER AND INVENTORY FOR UPDATE NOWAIT PASS")
