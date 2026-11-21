from uuid import uuid4

import pytest
from test_consumers import consume

from app.core.exceptions import ConflictError, NotFoundError, TransientError
from app.events.contracts import DomainEvent, EventNames
from app.models.entities import AuditLog, FailedJob, OutboxEvent, Payment, Shipment, Tenant
from app.models.enums import JobStatus, OrderStatus, PaymentStatus
from app.services.failed_jobs import record_failure, replay_failed_job


def failed_job(db, order):
    event = DomainEvent(
        event_type=EventNames.PAYMENT_AUTHORIZED, aggregate_id=order.id, tenant_id=order.tenant_id, payload={}
    )
    return record_failure(db, event.model_dump(mode="json"), TransientError("offline"), 3, "shipments", "test")


def test_recovery_reopens_compensated_order_and_finishes_once(db, workflow):
    order, stock = workflow
    consume(order, EventNames.ORDER_CREATED)
    consume(order, EventNames.INVENTORY_RESERVED)
    job = failed_job(db, order)
    old_event = job.event_id
    assert order.status == OrderStatus.FAILED and stock.available_quantity == 10
    assert db.query(Payment).one().status == PaymentStatus.REFUNDED
    row = replay_failed_job(db, job.id, actor=order.customer_id, tenant_id=order.tenant_id)
    assert order.status == OrderStatus.CREATED and order.failure_reason is None
    assert row.event_id != old_event and row.event_type == EventNames.ORDER_CREATED
    assert row.tenant_id == order.tenant_id and job.status == JobStatus.REPLAYED
    with pytest.raises(ConflictError) as error:
        replay_failed_job(db, job.id, actor=order.customer_id)
    assert error.value.code == "job_already_replayed"
    consume(order, EventNames.ORDER_CREATED, DomainEvent.model_validate(row.payload))
    consume(order, EventNames.INVENTORY_RESERVED)
    consume(order, EventNames.PAYMENT_AUTHORIZED)
    assert order.status == OrderStatus.FULFILLMENT_REQUESTED
    assert stock.available_quantity == 9 and stock.reserved_quantity == 1
    assert db.query(Payment).filter_by(status=PaymentStatus.AUTHORIZED).count() == 1
    assert db.query(Shipment).count() == 1
    assert db.query(AuditLog).filter_by(action="payment_refunded").count() == 1
    assert db.query(AuditLog).filter_by(action="order_reopened").count() == 1


@pytest.mark.parametrize(
    "status,reason",
    [(OrderStatus.FAILED, "PAYMENT_DECLINED"), (OrderStatus.CANCELLED, None), (OrderStatus.DELIVERED, None)],
)
def test_recovery_rejects_business_and_terminal_orders(db, workflow, status, reason):
    order, _ = workflow
    job = failed_job(db, order)
    order.status, order.failure_reason = status, reason
    db.commit()
    with pytest.raises(ConflictError) as error:
        replay_failed_job(db, job.id, actor=order.customer_id)
    assert error.value.code == "order_not_retryable"
    db.refresh(job)
    assert job.status == JobStatus.FAILED


def test_recovery_scope_suspension_discard_and_missing_order(db, workflow):
    from app.services.failed_jobs import discard_failed_job

    order, _ = workflow
    job = failed_job(db, order)
    with pytest.raises(NotFoundError):
        replay_failed_job(db, job.id, actor=order.customer_id, tenant_id=uuid4())
    tenant = db.get(Tenant, order.tenant_id)
    tenant.status = "suspended"
    db.commit()
    with pytest.raises(ConflictError):
        replay_failed_job(db, job.id, actor=order.customer_id)
    tenant.status = "active"
    db.commit()
    discard_failed_job(db, job.id, actor=order.customer_id, tenant_id=order.tenant_id)
    assert job.status.value == "discarded"
    assert db.query(AuditLog).filter_by(action="failed_job_discarded").count() == 1
    with pytest.raises(NotFoundError):
        discard_failed_job(db, uuid4(), actor=order.customer_id)
    with pytest.raises(ConflictError):
        replay_failed_job(db, job.id, actor=order.customer_id)
    orphan = FailedJob(task_name="test", queue_name="orders", payload={}, error_message="missing", aggregate_id=uuid4())
    db.add(orphan)
    db.commit()
    with pytest.raises(ConflictError) as error:
        replay_failed_job(db, orphan.id, actor=order.customer_id)
    assert error.value.code == "order_not_retryable"
    assert db.query(OutboxEvent).count() == 0


def test_nonterminal_replay_creates_missing_original_intent(db, workflow):
    order, _ = workflow
    event = DomainEvent(
        event_type=EventNames.ORDER_CREATED, aggregate_id=order.id, tenant_id=order.tenant_id, payload={}
    )
    job = FailedJob(
        tenant_id=order.tenant_id,
        task_name="test",
        queue_name="orders",
        payload=event.model_dump(mode="json"),
        error_message="offline",
        aggregate_id=order.id,
        event_id=event.event_id,
    )
    db.add(job)
    db.commit()
    row = replay_failed_job(db, job.id, actor=order.customer_id, tenant_id=order.tenant_id)
    assert row.event_id == event.event_id and row.queue == "orders"
    assert order.status == OrderStatus.CREATED and job.status == JobStatus.REPLAYED
