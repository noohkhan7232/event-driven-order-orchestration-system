"""One database clock and grace boundary for captures and scheduled expiry."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.principal import aware
from app.core.config import get_settings


def database_now(db: Session) -> datetime:
    return aware(db.scalar(select(func.now())) or datetime.now(UTC))


def capture_cutoff(now: datetime) -> datetime:
    return aware(now) - timedelta(seconds=get_settings().payment_capture_grace_seconds)


def capture_expired(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is not None and aware(expires_at) <= capture_cutoff(now)
