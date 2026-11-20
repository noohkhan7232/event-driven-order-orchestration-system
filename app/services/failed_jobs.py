"""Persist poison/exhausted messages and replay their original identity once."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.domain.order_state import TRANSITIONS, transition
from app.events.contracts import DomainEvent
from app.models.entities import FailedJob, OutboxEvent
from app.models.enums import JobStatus, OrderStatus
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.services.inventory import InventoryService
from app.services.payments import PaymentService


def record_failure(
    db: Session, raw_event: dict, error: Exception, retries: int, queue: str, task_name: str
) -> FailedJob:
    event = DomainEvent.model_validate(raw_event)
    order = OrderRepository(db).get_with_details(event.aggregate_id, for_update=True)
    if order and OrderStatus.FAILED in TRANSITIONS.get(order.status, set()):
        InventoryService(db).release_for_order(order)
        PaymentService(db).refund(order)
        transition(order, OrderStatus.FAILED, db=db, reason="RETRIES_EXHAUSTED")
    job = FailedJob(
        task_name=task_name,
        queue_name=queue,
        status=JobStatus.FAILED,
        payload=raw_event,
        event_id=event.event_id,
        aggregate_id=event.aggregate_id,
        error_message=str(error),
        retry_count=retries,
    )
    db.add(job)
    db.commit()
    return job


def replay_failed_job(db: Session, job_id: UUID) -> OutboxEvent:
    try:
        job = db.scalar(select(FailedJob).where(FailedJob.id == job_id).with_for_update())
        if job is None:
            raise NotFoundError("Failed job not found")
        event = DomainEvent.model_validate(job.payload)
        row = db.scalar(select(OutboxEvent).where(OutboxEvent.event_id == event.event_id).with_for_update())
        if row is None:
            row = OutboxRepository(db).add(event, job.queue_name)
        elif job.status != JobStatus.REPLAYED:
            # Existing published intent can be sent again with its original identity.
            from datetime import UTC, datetime

            row.status = "pending"
            row.available_at = datetime.now(UTC)
            row.attempts = 0
            row.last_error = None
            row.published_at = None
        job.status = JobStatus.REPLAYED
        db.commit()
        return row
    except Exception:
        db.rollback()
        raise
