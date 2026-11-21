"""Apply verified financial notifications under an owned order lock."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.principal import aware
from app.core.exceptions import TransientError
from app.events.contracts import DomainEvent
from app.models.entities import AuditLog, InboundWebhookEvent, Order, Payment, ProcessedEvent, Refund
from app.models.enums import OrderStatus, PaymentStatus
from app.providers.contracts import ProviderEvent, minor_units
from app.repositories.orders import OrderRepository
from app.services.payment_events import apply_capture
from app.services.refund_intents import request_refund, succeed_refund
from app.services.refunds import fail_refund


def captured_charge(db: Session, order: Order, payment: Payment, notice: ProviderEvent, event: DomainEvent) -> None:
    known = db.scalar(
        select(Payment)
        .join(Order)
        .where(
            Order.tenant_id == order.tenant_id,
            Payment.provider == payment.provider,
            Payment.provider_transaction_id == notice.provider_payment_id,
        )
    )
    if known and known.status in {PaymentStatus.AUTHORIZED, PaymentStatus.CAPTURED, PaymentStatus.REFUNDED}:
        return
    now = db.scalar(select(func.now())) or datetime.now(UTC)
    expired = order.payment_expires_at is not None and aware(order.payment_expires_at) <= aware(now)
    if order.status == OrderStatus.PAYMENT_PENDING and not expired:
        reason = (
            "AMOUNT_MISMATCH"
            if notice.amount_minor != minor_units(order.total_amount) or notice.currency != order.currency
            else None
        )
    else:
        reason = (
            "LATE_PAYMENT"
            if expired or order.status in {OrderStatus.FAILED, OrderStatus.CANCELLED}
            else "DUPLICATE_PAYMENT"
        )
    if reason is None:
        apply_capture(db, order, payment, notice, event)
        return
    charge = Payment(
        order_id=order.id,
        provider=payment.provider,
        provider_reference=notice.provider_order_id,
        provider_transaction_id=notice.provider_payment_id,
        amount=Decimal(notice.amount_minor) / Decimal(100),
        status=PaymentStatus.CAPTURED,
        provider_status="captured",
    )
    db.add(charge)
    db.flush()
    db.add(AuditLog(entity_type="order", entity_id=order.id, action=reason, payload={}))
    request_refund(db, order, charge, reason, notice.currency, event.correlation_id)


def refund_notice(db: Session, order: Order, payment: Payment, notice: ProviderEvent) -> None:
    refund = db.scalar(
        select(Refund)
        .where(Refund.tenant_id == order.tenant_id, Refund.payment_id == payment.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if refund is None or refund.status == "succeeded":
        return
    if (
        minor_units(refund.amount) != notice.amount_minor
        or refund.currency != notice.currency
        or (refund.provider_refund_id is not None and refund.provider_refund_id != notice.provider_refund_id)
    ):
        db.add(AuditLog(entity_type="refund", entity_id=refund.id, action="REFUND_REFERENCE_MISMATCH", payload={}))
        return
    if notice.provider_refund_id is None:
        raise TransientError("Missing refund identity")
    if notice.type == "refund_processed":
        succeed_refund(db, refund, payment, notice.provider_refund_id)
    else:
        refund.provider_refund_id = notice.provider_refund_id
        fail_refund(db, refund, "provider_refund_failed")


def process_provider_event(db: Session, event: DomainEvent) -> None:
    try:
        receipt = db.get(InboundWebhookEvent, event.aggregate_id)
        if receipt is None or receipt.tenant_id != event.tenant_id:
            raise TransientError("Payment receipt unavailable")
        if receipt.status in {"processed", "ignored"}:
            db.rollback()
            return
        notices = [ProviderEvent(**fields, raw={}) for fields in receipt.payload["events"]]
        if not notices:
            receipt = db.scalars(
                select(InboundWebhookEvent).where(InboundWebhookEvent.id == receipt.id).with_for_update()
            ).one()
            receipt.status = "ignored"
            db.commit()
            return
        # Parser currently produces one normalized notification per provider delivery.
        (notice,) = notices
        query = (
            select(Payment)
            .join(Order)
            .where(Order.tenant_id == receipt.tenant_id, Payment.provider == receipt.provider)
        )
        if notice.type.startswith("refund_"):
            query = query.where(Payment.provider_transaction_id == notice.provider_payment_id)
        else:
            query = query.where(Payment.provider_reference == notice.provider_order_id).order_by(
                Payment.idempotency_key.is_not(None).desc(), Payment.created_at.desc()
            )
        payment = db.scalar(query)
        if payment is None:
            raise TransientError("Payment reference not yet available")
        order = OrderRepository(db).get_with_details(payment.order_id, tenant_id=receipt.tenant_id, for_update=True)
        if order is None:
            raise TransientError("Payment order unavailable")
        receipt = db.scalars(
            select(InboundWebhookEvent)
            .where(InboundWebhookEvent.id == receipt.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        if receipt.status in {"processed", "ignored"}:
            db.commit()
            return
        payment = next(row for row in order.payments if row.id == payment.id)
        if notice.type == "payment_captured":
            captured_charge(db, order, payment, notice, event)
        elif notice.type == "payment_failed":
            if order.status == OrderStatus.PAYMENT_PENDING and payment.status == PaymentStatus.INITIATED:
                payment.provider_status = "failed"
                db.add(AuditLog(entity_type="payment", entity_id=payment.id, action="payment_failed", payload={}))
        else:
            refund_notice(db, order, payment, notice)
        receipt.status = "processed"
        db.add(ProcessedEvent(consumer="payment_provider", event_id=event.event_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
