from contextlib import nullcontext
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.core.exceptions import NotFoundError, TransientError
from app.events.contracts import DomainEvent, EventNames
from app.models.entities import FailedJob, OutboxEvent
from app.models.enums import JobStatus
from app.repositories.outbox import OutboxRepository
from app.services.failed_jobs import replay_failed_job
from app.workers import outbox_relay, tasks


def test_malformed_event_uses_retryable_taxonomy():
    with pytest.raises(TransientError):
        tasks.process_event.run({"not": "a domain event"})


@pytest.mark.parametrize("fails", [False, True])
def test_relay_loop_shutdown_cleanup_and_error_recovery(db, monkeypatch, fails):
    stopped = Mock()
    stopped.is_set.side_effect = [False, True]
    monkeypatch.setattr(outbox_relay, "Event", lambda: stopped)
    monkeypatch.setattr(outbox_relay.signal, "signal", lambda *a: None)
    monkeypatch.setattr(outbox_relay, "configure_logging", lambda: None)
    monkeypatch.setattr(outbox_relay, "SessionLocal", lambda: nullcontext(db))
    if fails:
        monkeypatch.setattr(outbox_relay, "relay_once", lambda *a: (_ for _ in ()).throw(OSError("temporary")))
    outbox_relay.main()
    stopped.wait.assert_called_once()


def test_relay_commit_failure_rolls_back(db, monkeypatch):
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(OSError("database unavailable")))
    with pytest.raises(OSError, match="database unavailable"):
        outbox_relay.relay_once(db, 1)
    assert not db.in_transaction()


def test_replay_reschedules_original_outbox_and_missing_job_rolls_back(db):
    event = DomainEvent(event_type=EventNames.ORDER_CREATED, aggregate_id=uuid4(), payload={})
    row = OutboxRepository(db).add(event, "orders")
    row.status, row.published_at, row.attempts = "published", datetime.now(UTC), 3
    job = FailedJob(
        task_name="test",
        queue_name="orders",
        payload=event.model_dump(mode="json"),
        error_message="offline",
        event_id=event.event_id,
        aggregate_id=event.aggregate_id,
    )
    db.add(job)
    db.commit()
    assert replay_failed_job(db, job.id).id == row.id
    assert row.status == "pending" and row.attempts == 0 and row.published_at is None
    assert job.status == JobStatus.REPLAYED
    assert db.query(OutboxEvent).count() == 1
    with pytest.raises(NotFoundError):
        replay_failed_job(db, uuid4())


def test_failure_hook_fallback_preserves_poison_event(db, workflow, monkeypatch):
    monkeypatch.setattr(tasks, "record_failure", lambda *a: (_ for _ in ()).throw(OSError("compensation unavailable")))
    raw = {"poison": "full original JSON"}
    tasks.process_event.push_request(retries=3, delivery_info={"routing_key": "orders"})
    try:
        tasks.process_event.on_failure(TransientError("offline"), str(uuid4()), [raw], {}, None)
        assert db.query(FailedJob).one().payload == raw
        monkeypatch.setattr(db, "add", lambda *a: (_ for _ in ()).throw(OSError("database unavailable")))
        tasks.process_event.on_failure(TransientError("offline"), str(uuid4()), [raw], {}, None)
    finally:
        tasks.process_event.pop_request()


def test_missing_order_and_scheduled_task_controls(db, workflow):
    tasks.process_event.run(
        DomainEvent(event_type=EventNames.ORDER_CREATED, aggregate_id=uuid4(), payload={}).model_dump(mode="json")
    )
    assert tasks.check_low_stock.run() == ["SKU"]
    assert tasks.detect_delayed_shipments.run() == 0
