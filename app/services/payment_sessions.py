"""Commit session intent, call a detached provider, then conditionally commit its result."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.principal import aware
from app.core.config import get_settings
from app.core.exceptions import TransientError
from app.domain.order_state import transition
from app.events.contracts import DomainEvent, EventNames
from app.models.entities import Order, Payment, ProcessedEvent
from app.models.enums import OrderStatus, PaymentStatus
from app.providers.contracts import PaymentAttempt, ProviderDeclined, SessionOrder
from app.providers.mock_v2 import MockPaymentProviderV2
from app.providers.resolution import resolve_payment_provider
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.services.inbound_payments import stage_receipt
from app.services.inventory import InventoryService
from app.services.shipping import ShippingService


def marked(db: Session, event: DomainEvent) -> bool:
    return (
        db.scalar(
            select(ProcessedEvent.event_id).where(
                ProcessedEvent.consumer == "order_workflow", ProcessedEvent.event_id == event.event_id
            )
        )
        is not None
    )


def finish(db: Session, event: DomainEvent) -> None:
    db.add(ProcessedEvent(consumer="order_workflow", event_id=event.event_id))
    db.commit()


def emit(db: Session, order: Order, kind: str, event: DomainEvent) -> None:
    OutboxRepository(db).add(
        DomainEvent(
            event_type=kind,
            aggregate_id=order.id,
            tenant_id=order.tenant_id,
            correlation_id=event.correlation_id,
            payload={"order_id": str(order.id), "reason": order.failure_reason},
        ),
        "shipments" if kind.startswith("shipment.") else "payments",
    )


def handle_payment(db: Session, order: Order, event: DomainEvent, retries: int) -> None:
    payment: Payment | None
    if marked(db, event):
        db.commit()
        return
    if order.status not in {OrderStatus.INVENTORY_RESERVED, OrderStatus.PAYMENT_PENDING}:
        finish(db, event)
        return
    if order.payment_method == "cod":
        payment = Payment(
            order_id=order.id,
            provider="cod",
            amount=order.total_amount,
            status=PaymentStatus.INITIATED,
            provider_status="pending_collection",
        )
        db.add(payment)
        try:
            ShippingService(db).create_shipment(order)
        except ProviderDeclined:
            payment.status = PaymentStatus.FAILED
            InventoryService(db).release_for_order(order)
            transition(order, OrderStatus.FAILED, db=db, reason="SHIPMENT_FAILED", correlation_id=event.correlation_id)
            emit(db, order, "shipment.failed", event)
        else:
            transition(order, OrderStatus.FULFILLMENT_REQUESTED, db=db, correlation_id=event.correlation_id)
            emit(db, order, EventNames.SHIPMENT_CREATED, event)
        finish(db, event)
        return
    settings = get_settings()
    provider = resolve_payment_provider(db, order.tenant_id, settings)
    if order.status == OrderStatus.INVENTORY_RESERVED:
        payment = Payment(
            order_id=order.id,
            provider=provider.name,
            amount=order.total_amount,
            status=PaymentStatus.INITIATED,
            provider_status="creating",
            idempotency_key=f"{order.id}:payment:{len(order.payments) + 1}",
        )
        db.add(payment)
        order.payment_expires_at = datetime.now(UTC) + timedelta(seconds=settings.payment_window_seconds)
        transition(order, OrderStatus.PAYMENT_PENDING, db=db, correlation_id=event.correlation_id)
        db.flush()
    else:
        payment = max(
            (p for p in order.payments if p.status == PaymentStatus.INITIATED and p.idempotency_key),
            key=lambda p: (p.created_at, p.id),
            default=None,
        )
        if payment is None or payment.provider_reference:
            finish(db, event)
            return
    if payment.provider != provider.name or not order.payment_expires_at or not payment.idempotency_key:
        raise TransientError("Payment configuration changed during session creation")
    snapshot = SessionOrder(
        order.id,
        order.tenant_id,
        Decimal(order.total_amount),
        order.currency,
        tuple(item.sku for item in order.items),
        aware(order.payment_expires_at),
    )
    attempt = PaymentAttempt(payment.id, retries)
    key = payment.idempotency_key
    db.commit()
    try:
        result = provider.create_payment_session(snapshot, attempt, key)
        reason = None
    except ProviderDeclined as error:
        result = None
        reason = (
            "PAYMENT_DECLINED"
            if provider.name == "mock" and str(error) == "PAYMENT_DECLINED"
            else "PAYMENT_SESSION_FAILED"
        )
    locked = OrderRepository(db).get_with_details(snapshot.id, tenant_id=snapshot.tenant_id, for_update=True)
    if locked is None or marked(db, event):
        db.commit()
        return
    current = next(p for p in locked.payments if p.id == attempt.id)
    if locked.status == OrderStatus.PAYMENT_PENDING and current.provider_reference is None:
        if result:
            current.provider_reference = result.provider_order_id
            current.checkout_payload = {
                key: result.checkout_payload[key]
                for key in ("provider", "provider_order_id", "amount_minor", "currency", "key_id")
            }
            current.provider_status = "pending"
            if isinstance(provider, MockPaymentProviderV2) and settings.mock_payment_auto_complete:
                headers, body = provider.signed_capture(result)
                (notice,) = provider.verify_and_parse_webhook(headers, body, settings.mock_webhook_secret)
                stage_receipt(db, provider.name, locked.tenant_id, notice.event_id, [notice], event.correlation_id)
        else:
            current.status = PaymentStatus.FAILED
            current.provider_status = "failed"
            current.failure_code = "SIMULATED_DECLINE" if reason == "PAYMENT_DECLINED" else reason
            InventoryService(db).release_for_order(locked)
            transition(locked, OrderStatus.FAILED, db=db, reason=reason, correlation_id=event.correlation_id)
            emit(db, locked, EventNames.PAYMENT_FAILED, event)
    finish(db, event)
