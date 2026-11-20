"""The only writer of an existing order's lifecycle status."""

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import InvalidTransitionError
from app.models.entities import AuditLog, Order
from app.models.enums import OrderStatus as S

TRANSITIONS: dict[S, set[S]] = {
    S.CREATED: {S.INVENTORY_RESERVED, S.FAILED, S.CANCELLED},
    S.INVENTORY_RESERVED: {S.PAYMENT_PENDING, S.FAILED, S.CANCELLED},
    S.PAYMENT_PENDING: {S.PAID, S.FAILED, S.CANCELLED},
    S.PAID: {S.FULFILLMENT_REQUESTED, S.FAILED, S.CANCELLED},
    S.FULFILLMENT_REQUESTED: {S.SHIPPED, S.CANCELLED},
    S.SHIPPED: {S.DELIVERED},
}


def transition(
    order: Order, new_status: S, *, db: Session, reason: str | None = None, actor: UUID | None = None
) -> None:
    previous = order.status
    if new_status not in TRANSITIONS.get(previous, set()):
        raise InvalidTransitionError(f"Cannot transition order from {previous.value} to {new_status.value}")
    order.status = new_status
    if reason is not None:
        order.failure_reason = reason
    db.add(
        AuditLog(
            actor_id=actor,
            entity_type="order",
            entity_id=order.id,
            action="order_transition",
            payload={"from": previous.value, "to": new_status.value, "reason": reason},
        )
    )
