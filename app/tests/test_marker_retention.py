from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from app.models.entities import FailedJob, ProcessedEvent


def test_marker_retention_strict_boundary_and_failed_jobs_retained(db, monkeypatch):
    from app.workers.outbox_relay import cleanup_processed

    now = db.scalar(select(func.now()))
    cutoff = now - timedelta(days=30)
    ids = [uuid4() for _ in range(3)]
    for event_id, moment in zip(
        ids, [cutoff - timedelta(seconds=1), cutoff, cutoff + timedelta(seconds=1)], strict=True
    ):
        db.add(ProcessedEvent(consumer="test", event_id=event_id, processed_at=moment))
    db.add(
        FailedJob(
            task_name="test",
            queue_name="orders",
            payload={},
            error_message="retained",
            created_at=now - timedelta(days=365),
        )
    )
    db.commit()
    # Hold the database-derived clock constant while checking the exact boundary;
    # otherwise PostgreSQL's next transaction advances it by microseconds.
    monkeypatch.setattr(db, "scalar", lambda *args, **kwargs: now)
    assert cleanup_processed(db) == 1
    assert set(db.scalars(select(ProcessedEvent.event_id))) == set(ids[1:])
    assert db.query(FailedJob).count() == 1
