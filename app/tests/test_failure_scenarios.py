from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_consumers import consume

from app.core.config import Settings, get_settings
from app.core.exceptions import TransientError
from app.events.contracts import DomainEvent, EventNames
from app.models.entities import AuditLog, FailedJob, OutboxEvent, Payment, ProcessedEvent, Shipment
from app.models.enums import OrderStatus, PaymentStatus
from app.workers import tasks


def test_failure_injection_forbidden_in_production():
    with pytest.raises(ValidationError, match="injection"):
        Settings(_env_file=None, environment="production", secret_key="x" * 32, failure_injection_enabled=True)


@pytest.mark.parametrize(
    "prefix,reason", [("FAIL-PAYMENT-", "PAYMENT_DECLINED"), ("FAIL-SHIPMENT-", "SHIPMENT_FAILED")]
)
def test_business_failure_compensates_without_retry(db, workflow, monkeypatch, prefix, reason):
    order, stock = workflow
    stock.sku = order.items[0].sku = prefix + "TEST"
    db.commit()
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: get_settings().model_copy(update={"failure_injection_enabled": True}),
        raising=False,
    )
    consume(order, EventNames.ORDER_CREATED)
    consume(order, EventNames.INVENTORY_RESERVED)
    if prefix == "FAIL-SHIPMENT-":
        consume(order, EventNames.PAYMENT_AUTHORIZED)
        assert db.query(Payment).one().status == PaymentStatus.REFUNDED
        assert db.query(AuditLog).filter_by(action="payment_refunded").count() == 1
        assert db.query(AuditLog).filter_by(action="inventory_released").count() == 1
    assert order.status == OrderStatus.FAILED and order.failure_reason == reason
    assert stock.available_quantity == 10 and stock.reserved_quantity == 0
    assert db.query(Shipment).count() == 0


def test_transient_injection_then_retry_has_once_only_effects(db, workflow, monkeypatch):
    order, stock = workflow
    stock.sku = order.items[0].sku = "FAIL-TRANSIENT-TEST"
    db.commit()
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: get_settings().model_copy(update={"failure_injection_enabled": True, "failure_transient_attempts": 1}),
        raising=False,
    )
    event = DomainEvent(event_type=EventNames.ORDER_CREATED, aggregate_id=order.id, payload={})
    with pytest.raises(TransientError):
        consume(order, event.event_type, event)
    assert stock.available_quantity == 10
    assert db.query(ProcessedEvent).count() == 0
    tasks.process_event.push_request(retries=1)
    try:
        consume(order, event.event_type, event)
    finally:
        tasks.process_event.pop_request()
    consume(order, event.event_type, event)
    assert stock.available_quantity == 9 and stock.reserved_quantity == 1


def test_exhaustion_persists_full_event_compensates_and_replay_is_once(db, workflow, monkeypatch):
    from app.services.failed_jobs import replay_failed_job

    order, stock = workflow
    consume(order, EventNames.ORDER_CREATED)
    event = DomainEvent(
        event_type=EventNames.INVENTORY_RESERVED,
        aggregate_id=order.id,
        payload={"original": "complete"},
        correlation_id="failed-trace",
    )
    tasks.process_event.push_request(retries=3, delivery_info={"routing_key": "payments"})
    try:
        tasks.process_event.on_failure(
            TransientError("offline"), str(uuid4()), [event.model_dump(mode="json")], {}, None
        )
    finally:
        tasks.process_event.pop_request()
    db.expire_all()
    job = db.query(FailedJob).one()
    assert job.payload == event.model_dump(mode="json")
    assert job.event_id == event.event_id and job.aggregate_id == order.id
    assert order.status == OrderStatus.FAILED and not order.inventory_reserved
    assert stock.available_quantity == 10
    first = replay_failed_job(db, job.id)
    second = replay_failed_job(db, job.id)
    assert first.event_id == second.event_id == event.event_id
    assert db.query(OutboxEvent).filter_by(event_id=event.event_id).count() == 1
    assert job.status.value == "replayed"
    consume(order, event.event_type, event)
    assert stock.available_quantity == 10  # A terminal saga is never resurrected by replay.


def test_signup_duplicate_insert_race_is_conflict(client, monkeypatch):
    from app.repositories.users import UserRepository

    payload = {
        "tenant_slug": "default",
        "email": "race@example.com",
        "password": "strong-password",
        "full_name": "Race User",
    }
    assert client.post("/api/v1/auth/signup", json=payload).status_code == 201
    monkeypatch.setattr(UserRepository, "get_by_email", lambda *a: None)
    assert client.post("/api/v1/auth/signup", json=payload).status_code == 409
