from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_provider_events import deliver
from test_provider_events import pending_payment as pending_payment

from app.core.exceptions import TransientError
from app.events.contracts import DomainEvent
from app.models.entities import AuditLog, FailedJob, InboundWebhookEvent, OutboxEvent, Refund
from app.models.enums import OrderStatus, PaymentStatus
from app.providers.contracts import ProviderEvent
from app.services.inbound_payments import stage_receipt
from app.services.provider_events import process_provider_event
from app.workers import tasks


def queue_notice(db, order, notice, age=0):
    identity = stage_receipt(db, "razorpay", order.tenant_id, notice.event_id, [notice])
    receipt = db.get(InboundWebhookEvent, identity)
    receipt.received_at = db.scalar(select(func.now())) - timedelta(seconds=age)
    db.commit()
    event = DomainEvent.model_validate(
        db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == identity)).payload
    )
    return receipt, event


@pytest.mark.parametrize("kind", ["payment", "refund_payment", "refund_row"])
def test_unmatched_notice_is_ignored_at_deadline_without_failed_job(db, pending_payment, kind):
    order, payment, _ = pending_payment
    if kind == "refund_row":
        deliver(db, order)
    notice = ProviderEvent(
        "evt_" + uuid4().hex,
        "payment_captured" if kind == "payment" else "refund_processed",
        "order_unknown" if kind == "payment" else None,
        "pay_provider" if kind == "refund_row" else "pay_unknown",
        None if kind == "payment" else "rfnd_unknown",
        1000,
        "INR",
        {},
    )
    receipt, event = queue_notice(db, order, notice, age=600)
    for _ in range(2):
        tasks.process_event.run(event.model_dump(mode="json"))
    assert receipt.status == "ignored"
    audit = db.query(AuditLog).filter_by(entity_id=receipt.id, action="UNMATCHED_PAYMENT").one()
    assert audit.payload == {} and audit.entity_type == "inbound_webhook_event"
    assert db.query(FailedJob).count() == db.query(Refund).count() == 0
    assert payment.status == (PaymentStatus.AUTHORIZED if kind == "refund_row" else PaymentStatus.INITIATED)


def test_legitimate_payment_reference_arriving_within_window_is_processed(db, pending_payment):
    order, payment, _ = pending_payment
    payment.provider_reference = None
    db.commit()
    notice = ProviderEvent("evt_wait", "payment_captured", "order_arriving", "pay_later", None, 1000, "INR", {})
    receipt, event = queue_notice(db, order, notice, age=590)
    with pytest.raises(TransientError):
        process_provider_event(db, event)
    assert receipt.status == "received" and db.query(FailedJob).count() == 0
    payment.provider_reference = "order_arriving"
    db.commit()
    process_provider_event(db, event)
    assert order.status == OrderStatus.PAID and receipt.status == "processed"
    assert db.query(AuditLog).filter_by(action="UNMATCHED_PAYMENT").count() == 0


def test_legitimate_refund_request_arriving_within_window_is_processed(db, pending_payment):
    from app.services.refund_intents import request_refund

    order, payment, _ = pending_payment
    deliver(db, order)
    notice = ProviderEvent("evt_refund_wait", "refund_processed", None, "pay_provider", "rfnd_later", 1000, "INR", {})
    receipt, event = queue_notice(db, order, notice, age=590)
    with pytest.raises(TransientError):
        process_provider_event(db, event)
    request_refund(db, order, payment, "ORDER_CANCELLED")
    db.commit()
    process_provider_event(db, event)
    assert receipt.status == "processed" and db.query(Refund).one().status == "succeeded"
    assert payment.status == PaymentStatus.REFUNDED and db.query(FailedJob).count() == 0


def test_unmatched_worker_retry_outlives_ordinary_retry_count(db, pending_payment, monkeypatch):
    from celery.exceptions import Retry

    from app.services.provider_events import UnmatchedPayment

    order, _, _ = pending_payment
    notice = ProviderEvent("evt_retry", "payment_captured", "order_absent", "pay_absent", None, 1000, "INR", {})
    _, event = queue_notice(db, order, notice, age=590)
    calls = []

    def retry(**kwargs):
        calls.append(kwargs)
        raise Retry()

    monkeypatch.setattr(tasks.process_event, "retry", retry)
    tasks.process_event.push_request(retries=100, called_directly=False)
    try:
        with pytest.raises(Retry):
            tasks.process_event.run(event.model_dump(mode="json"))
    finally:
        tasks.process_event.pop_request()
    assert isinstance(calls[0]["exc"], UnmatchedPayment)
    assert 1 <= calls[0]["countdown"] <= 10 and calls[0]["max_retries"] is None
    assert tasks.process_event.max_retries is None
    assert tasks.process_event.retry_kwargs["max_retries"] == 3


@pytest.mark.parametrize("seconds,accepted", [(119, True), (120, False), (121, False)])
def test_capture_grace_inside_exact_boundary_and_outside(db, pending_payment, monkeypatch, seconds, accepted):
    from app.auth.principal import aware
    from app.services import provider_events

    order, payment, stock = pending_payment
    now = aware(db.scalar(select(func.now())))
    monkeypatch.setattr(provider_events, "database_now", lambda db: now, raising=False)
    order.payment_expires_at = now - timedelta(seconds=seconds)
    db.commit()
    deliver(db, order)
    if accepted:
        assert order.status == OrderStatus.PAID and payment.status == PaymentStatus.AUTHORIZED
        assert db.query(Refund).count() == 0
    else:
        assert order.status == OrderStatus.PAYMENT_PENDING and payment.status == PaymentStatus.INITIATED
        assert db.query(Refund).one().reason == "LATE_PAYMENT"
    assert (stock.available_quantity, stock.reserved_quantity) == (9, 1)


def test_terminal_order_never_uses_capture_grace(db, pending_payment, monkeypatch):
    from app.auth.principal import aware
    from app.domain.order_state import transition
    from app.services import provider_events

    order, _, _ = pending_payment
    now = aware(db.scalar(select(func.now())))
    monkeypatch.setattr(provider_events, "database_now", lambda db: now, raising=False)
    order.payment_expires_at = now - timedelta(seconds=1)
    transition(order, OrderStatus.CANCELLED, db=db)
    db.commit()
    deliver(db, order)
    assert order.status == OrderStatus.CANCELLED and db.query(Refund).one().reason == "LATE_PAYMENT"


def test_grace_is_configurable_and_missing_expiry_is_not_expired(monkeypatch):
    from datetime import UTC, datetime

    from app.core.config import get_settings
    from app.services.payment_timing import capture_cutoff, capture_expired

    assert get_settings().payment_capture_grace_seconds == 120
    now = datetime.now(UTC)
    assert not capture_expired(None, now)
    monkeypatch.setattr(get_settings(), "payment_capture_grace_seconds", 0)
    assert capture_cutoff(now) == now and capture_expired(now, now)
