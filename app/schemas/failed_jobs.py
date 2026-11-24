"""An explicit safe view; legacy payloads and arbitrary diagnostics stay private."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.entities import FailedJob
from app.models.enums import JobStatus
from app.workers.celery_app import celery_app

SAFE_ERRORS = frozenset({"RETRIES_EXHAUSTED", "PROVIDER_UNAVAILABLE", "refund_failed", "order_not_retryable"})
QUEUES = frozenset({"orders", "inventory", "payments", "shipments", "dead_letter"})


class FailedJobRead(BaseModel):
    id: UUID
    tenant_id: UUID | None
    task_name: str
    queue_name: str
    status: JobStatus
    event_id: UUID | None
    aggregate_id: UUID | None
    error_message: str = Field(max_length=160)
    retry_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_job(cls, row: FailedJob) -> "FailedJobRead":
        return cls(
            id=row.id,
            tenant_id=row.tenant_id,
            task_name=row.task_name if row.task_name in celery_app.conf.task_routes else "unknown",
            queue_name=row.queue_name if row.queue_name in QUEUES else "unknown",
            status=row.status,
            event_id=row.event_id,
            aggregate_id=row.aggregate_id,
            error_message=(
                row.error_message if row.error_message in SAFE_ERRORS else "Task failed; private diagnostics withheld."
            )[:160],
            retry_count=row.retry_count,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class ReplayAccepted(BaseModel):
    id: UUID
    status: Literal["replayed"] = "replayed"
    event_id: UUID
