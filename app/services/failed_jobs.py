"""Persist poison/exhausted messages and replay their original identity once."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.domain.order_state import TRANSITIONS, reopen_failed_order, transition
from app.events.contracts import DomainEvent, EventNames
from app.models.entities import AuditLog, FailedJob, Order, OutboxEvent, Tenant
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
        transition(order, OrderStatus.FAILED, db=db, reason="RETRIES_EXHAUSTED", correlation_id=event.correlation_id)
    job = FailedJob(
        task_name=task_name,
        tenant_id=order.tenant_id if order else event.tenant_id,
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


class RecoveryConflict(ConflictError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _locked_job(db: Session, job_id: UUID, tenant_id: UUID | None) -> tuple[FailedJob, Order | None]:
    conditions = [FailedJob.id == job_id]
    if tenant_id is not None:
        conditions.append(FailedJob.tenant_id == tenant_id)
    job = db.scalar(select(FailedJob).where(*conditions))
    if job is None:
        raise NotFoundError("Failed job not found")
    # Match worker lock ordering: aggregate before failed job. Re-read the job
    # after waiting so concurrent recoveries cannot both act on stale status.
    order = (
        OrderRepository(db).get_with_details(job.aggregate_id, tenant_id=tenant_id, for_update=True)
        if job.aggregate_id
        else None
    )
    job = db.scalar(select(FailedJob).where(*conditions).with_for_update().execution_options(populate_existing=True))
    if job is None:
        raise NotFoundError("Failed job not found")
    if job.status == JobStatus.REPLAYED:
        raise RecoveryConflict("job_already_replayed")
    if job.status == JobStatus.DISCARDED:
        raise RecoveryConflict("order_not_retryable")
    return job, order


def replay_failed_job(db: Session, job_id: UUID, *, actor: UUID, tenant_id: UUID | None = None) -> OutboxEvent:
    try:
        job, order = _locked_job(db, job_id, tenant_id)
        if order is None:
            raise RecoveryConflict("order_not_retryable")
        tenant = db.get(Tenant, order.tenant_id, populate_existing=True)
        if tenant is None or tenant.status != "active":
            raise RecoveryConflict("tenant_suspended")
        row: OutboxEvent | None
        if order.status == OrderStatus.FAILED and order.failure_reason == "RETRIES_EXHAUSTED":
            reopen_failed_order(order, db=db, actor=actor, reason=f"failed_job:{job.id}")
            event = DomainEvent(
                event_type=EventNames.ORDER_CREATED,
                aggregate_id=order.id,
                tenant_id=order.tenant_id,
                payload={"order_id": str(order.id)},
            )
            row = OutboxRepository(db).add(event, "orders")
        elif order.status not in {OrderStatus.FAILED, OrderStatus.CANCELLED, OrderStatus.DELIVERED}:
            event = DomainEvent.model_validate(job.payload)
            event.tenant_id = order.tenant_id
            row = db.scalar(select(OutboxEvent).where(OutboxEvent.event_id == event.event_id).with_for_update())
            if row is None:
                row = OutboxRepository(db).add(event, job.queue_name)
            else:
                row.status = "pending"
                row.available_at = datetime.now(UTC)
                row.attempts = 0
                row.last_error = None
                row.published_at = None
                row.tenant_id = order.tenant_id
                row.payload = event.model_dump(mode="json")
        else:
            raise RecoveryConflict("order_not_retryable")
        job.status = JobStatus.REPLAYED
        db.add(
            AuditLog(
                actor_id=actor,
                entity_type="failed_job",
                entity_id=job.id,
                action="failed_job_replayed",
                payload={"event_id": str(row.event_id)},
            )
        )
        db.commit()
        return row
    except Exception:
        db.rollback()
        raise


def discard_failed_job(db: Session, job_id: UUID, *, actor: UUID, tenant_id: UUID | None = None) -> None:
    try:
        job, _ = _locked_job(db, job_id, tenant_id)
        job.status = JobStatus.DISCARDED
        db.add(
            AuditLog(
                actor_id=actor, entity_type="failed_job", entity_id=job.id, action="failed_job_discarded", payload={}
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
