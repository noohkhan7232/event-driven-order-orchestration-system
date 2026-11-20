from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import auth_header

from app.events.contracts import DomainEvent


def test_order_outbox_atomicity(client, db, monkeypatch):
    from app.models.entities import Order, OutboxEvent
    from app.repositories.outbox import OutboxRepository

    headers = auth_header(client, "atomic@example.com", "strong-password")
    original = OutboxRepository.add

    def fail(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("after outbox add")

    monkeypatch.setattr(OutboxRepository, "add", fail)
    with pytest.raises(RuntimeError, match="after outbox add"):
        client.post(
            "/api/v1/orders",
            headers={**headers, "Idempotency-Key": "atomic"},
            json={"items": [{"sku": "SKU", "quantity": 1}]},
        )
    db.rollback()
    assert db.query(Order).count() == db.query(OutboxEvent).count() == 0


def test_broker_down_accepts_order_and_relay_publishes_once(client, db, monkeypatch):
    from app.models.entities import OutboxEvent
    from app.workers.outbox_relay import relay_once

    def down(*args, **kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr("app.events.publisher.celery_app.send_task", down)
    headers = auth_header(client, "outbox@example.com", "strong-password")
    request = {
        "headers": {**headers, "Idempotency-Key": "outbox", "x-request-id": "trace-123"},
        "json": {"items": [{"sku": "SKU", "quantity": 1}]},
    }
    assert client.post("/api/v1/orders", **request).status_code == 202
    assert client.post("/api/v1/orders", **request).status_code == 202
    row = db.query(OutboxEvent).one()
    assert row.status == "pending" and row.correlation_id == "trace-123"
    assert relay_once(db, 10) == 0
    assert row.attempts == 1 and row.status == "pending" and "broker down" in row.last_error
    row.available_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    sent = []
    monkeypatch.setattr("app.events.publisher.celery_app.send_task", lambda *a, **kw: sent.append(kw))
    assert relay_once(db, 10) == 1
    assert relay_once(db, 10) == 0
    assert row.status == "published" and row.published_at is not None
    assert sent[0]["task_id"] == str(row.event_id)
    assert len(sent) == 1


def test_relay_exhaustion_and_retention(db, monkeypatch):
    from app.core.config import get_settings
    from app.models.entities import OutboxEvent
    from app.repositories.outbox import OutboxRepository
    from app.workers.outbox_relay import cleanup_published, relay_once

    row = OutboxRepository(db).add(DomainEvent(event_type="test", aggregate_id=uuid4(), payload={}), "orders")
    db.commit()
    monkeypatch.setattr(
        "app.workers.outbox_relay.get_settings", lambda: get_settings().model_copy(update={"outbox_max_attempts": 1})
    )
    monkeypatch.setattr(
        "app.events.publisher.celery_app.send_task", lambda *a, **kw: (_ for _ in ()).throw(OSError("offline"))
    )
    assert relay_once(db, 1) == 0
    assert row.status == "failed" and row.attempts == 1
    old = OutboxEvent(
        event_id=uuid4(),
        aggregate_id=uuid4(),
        event_type="test",
        queue="orders",
        payload={},
        status="published",
        published_at=datetime.now(UTC) - timedelta(days=8),
    )
    db.add(old)
    db.commit()
    assert cleanup_published(db) == 1
    assert db.query(OutboxEvent).count() == 1
