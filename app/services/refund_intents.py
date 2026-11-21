"""Durable refund requests and terminal effects, always in the caller's transaction."""

from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.contextvars import get_contextvars

from app.events.contracts import DomainEvent
from app.models.entities import AuditLog, Order, Payment, Refund
from app.models.enums import PaymentStatus
from app.repositories.outbox import OutboxRepository


def request_refund(
    db: Session,
    order: Order,
    payment: Payment,
    reason: str,
    currency: str | None = None,
    correlation_id: str | None = None,
) -> Refund | None:
    if payment.provider == "cod":
        return None
    db.flush()
    key = f"{payment.id}:refund"
    existing = db.scalar(select(Refund).where(Refund.idempotency_key == key).with_for_update())
    if existing:
        return existing
    row = Refund(
        tenant_id=order.tenant_id,
        order_id=order.id,
        payment_id=payment.id,
        amount=payment.amount,
        currency=currency or order.currency,
        reason=reason,
        status="requested",
        attempts=0,
        idempotency_key=key,
        correlation_id=correlation_id or get_contextvars().get("request_id"),
    )
    db.add(row)
    db.flush()
    db.add(AuditLog(entity_type="refund", entity_id=row.id, action="refund_requested", payload={"reason": reason}))
    OutboxRepository(db).add(
        DomainEvent(
            event_type="refund.requested",
            aggregate_id=row.id,
            tenant_id=order.tenant_id,
            correlation_id=row.correlation_id,
            payload={"refund_id": str(row.id), "order_id": str(order.id)},
        ),
        "payments",
    )
    return row


def succeed_refund(db: Session, refund: Refund, payment: Payment, reference: str | None) -> None:
    if refund.status == "succeeded":
        return
    refund.provider_refund_id = reference
    refund.status, refund.last_error = "succeeded", None
    refund.claim_token, refund.lease_until = None, None
    payment.status, payment.provider_status = PaymentStatus.REFUNDED, "refunded"
    db.add(AuditLog(entity_type="payment", entity_id=payment.id, action="payment_refunded", payload={}))
    db.add(AuditLog(entity_type="refund", entity_id=refund.id, action="refund_succeeded", payload={}))
    OutboxRepository(db).add(
        DomainEvent(
            event_type="refund.succeeded",
            aggregate_id=refund.order_id,
            tenant_id=refund.tenant_id,
            correlation_id=refund.correlation_id,
            payload={"refund_id": str(refund.id), "order_id": str(refund.order_id)},
        ),
        "payments",
    )
