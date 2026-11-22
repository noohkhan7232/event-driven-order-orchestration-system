"""Append status history and durable notification intent in the caller's transaction."""

from sqlalchemy.orm import Session
from structlog.contextvars import get_contextvars

from app.events.contracts import DomainEvent, EventNames
from app.models.entities import Order, OrderTimeline
from app.models.enums import OrderStatus
from app.repositories.outbox import OutboxRepository

PUBLIC_REASON_CODES = frozenset(
    {
        "INSUFFICIENT_INVENTORY",
        "PAYMENT_DECLINED",
        "PAYMENT_SESSION_FAILED",
        "PAYMENT_EXPIRED",
        "SHIPMENT_FAILED",
        "RETRIES_EXHAUSTED",
    }
)


def safe_event_reason(reason: str | None) -> str | None:
    # Free-text cancellation/operator reasons stay private. Only declared domain
    # codes cross the event boundary, so arbitrary PII/secrets cannot be echoed.
    return reason if reason in PUBLIC_REASON_CODES else None


def record_initial_order(db: Session, order: Order) -> None:
    # Creation already emits order.created. Its timeline begins with no predecessor.
    db.add(OrderTimeline(tenant_id=order.tenant_id, order_id=order.id, from_status=None, to_status=order.status.value))


def record_status_change(
    db: Session, order: Order, previous: OrderStatus, *, reason: str | None, correlation_id: str | None = None
) -> None:
    db.add(
        OrderTimeline(
            tenant_id=order.tenant_id,
            order_id=order.id,
            from_status=previous.value,
            to_status=order.status.value,
            reason=reason,
        )
    )
    OutboxRepository(db).add(
        DomainEvent(
            event_type=EventNames.ORDER_STATUS_CHANGED,
            aggregate_id=order.id,
            tenant_id=order.tenant_id,
            correlation_id=correlation_id or get_contextvars().get("request_id"),
            payload={
                "order_id": str(order.id),
                "from": previous.value,
                "to": order.status.value,
                "reason": safe_event_reason(reason),
                "tenant_id": str(order.tenant_id),
            },
        ),
        "orders",
    )
