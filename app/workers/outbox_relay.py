"""At-least-once delivery. Database locks cover send and publish acknowledgement."""

import signal
from datetime import UTC, datetime, timedelta
from threading import Event

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.events.contracts import DomainEvent
from app.events.publisher import EventPublisher
from app.models.entities import OutboxEvent

logger = get_logger(__name__)


def relay_once(db: Session, batch_size: int = 100) -> int:
    settings = get_settings()
    now = datetime.now(UTC)
    statement = (
        select(OutboxEvent)
        .where(OutboxEvent.status == "pending", OutboxEvent.available_at <= now)
        .order_by(OutboxEvent.created_at, OutboxEvent.id)
        .limit(batch_size)
    )
    if db.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    count = 0
    try:
        for row in db.scalars(statement):
            try:
                EventPublisher().publish(DomainEvent.model_validate(row.payload), row.queue)
            except Exception as exc:
                row.attempts += 1
                row.last_error = str(exc)
                row.available_at = now + timedelta(seconds=min(2 ** min(row.attempts, 16), 3600))
                if row.attempts >= settings.outbox_max_attempts:
                    row.status = "failed"
                    logger.error("outbox_delivery_exhausted", event_id=str(row.event_id), error=str(exc))
            else:
                row.status = "published"
                row.published_at = datetime.now(UTC)
                row.last_error = None
                count += 1
        db.commit()
        return count
    except Exception:
        db.rollback()
        raise


def cleanup_published(db: Session) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=get_settings().outbox_retention_days)
    result = db.execute(delete(OutboxEvent).where(OutboxEvent.status == "published", OutboxEvent.published_at < cutoff))
    db.commit()
    return result.rowcount


def main() -> None:
    configure_logging()
    stopped = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    cleanup_at = datetime.now(UTC)
    while not stopped.is_set():
        try:
            with SessionLocal() as db:
                relay_once(db)
                if datetime.now(UTC) >= cleanup_at:
                    cleanup_published(db)
                    cleanup_at = datetime.now(UTC) + timedelta(hours=1)
        except Exception:
            logger.exception("outbox_relay_error")
        stopped.wait(get_settings().outbox_poll_interval_seconds)


if __name__ == "__main__":
    main()
