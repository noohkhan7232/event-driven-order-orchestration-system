"""Normalized capture handling shared by synthetic and future inbound delivery."""

from sqlalchemy.orm import Session

from app.core.exceptions import TransientError
from app.domain.order_state import transition
from app.events.contracts import DomainEvent, EventNames
from app.models.entities import AuditLog, Order, Payment
from app.models.enums import OrderStatus, PaymentStatus
from app.providers.contracts import ProviderEvent, minor_units
from app.repositories.outbox import OutboxRepository


def apply_capture(db: Session, order: Order, payment: Payment, notice: ProviderEvent, event: DomainEvent) -> None:
    if order.status != OrderStatus.PAYMENT_PENDING or payment.provider_reference != notice.provider_order_id:
        return
    if notice.type != "payment_captured":
        return
    if notice.amount_minor != minor_units(payment.amount) or notice.currency != order.currency:
        raise TransientError("Payment capture amount mismatch")
    payment.provider_transaction_id = notice.provider_payment_id
    payment.status = PaymentStatus.AUTHORIZED
    payment.provider_status = "captured"
    db.add(
        AuditLog(
            entity_type="payment",
            entity_id=order.id,
            action="payment_authorized",
            payload={"provider": payment.provider},
        )
    )
    transition(order, OrderStatus.PAID, db=db, correlation_id=event.correlation_id)
    OutboxRepository(db).add(
        DomainEvent(
            event_type=EventNames.PAYMENT_AUTHORIZED,
            aggregate_id=order.id,
            tenant_id=order.tenant_id,
            correlation_id=event.correlation_id,
            payload={"order_id": str(order.id), "reason": None},
        ),
        "shipments",
    )
