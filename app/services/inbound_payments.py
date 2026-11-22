"""Signature-verified, sanitized receipts and delivery intents share one transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import CredentialVault
from app.core.exceptions import DomainError, NotFoundError
from app.events.contracts import DomainEvent
from app.models.entities import InboundWebhookEvent, TenantPaymentConfig
from app.providers.contracts import ProviderDeclined, ProviderEvent
from app.providers.razorpay import parse_signed
from app.repositories.outbox import OutboxRepository


class InvalidWebhook(DomainError):
    status_code = 401
    code = "invalid_signature"

    def __init__(self) -> None:
        super().__init__("Invalid webhook")


def stage_receipt(
    db: Session,
    provider: str,
    tenant_id: UUID,
    event_id: str,
    notices: list[ProviderEvent],
    correlation_id: str | None = None,
) -> UUID:
    payload = {
        "events": [
            {
                name: getattr(notice, name)
                for name in (
                    "event_id",
                    "type",
                    "provider_order_id",
                    "provider_payment_id",
                    "provider_refund_id",
                    "amount_minor",
                    "currency",
                )
            }
            for notice in notices
        ]
    }
    if db.get_bind().dialect.name == "sqlite":
        connection = db.connection()
        if not getattr(connection.connection.driver_connection, "in_transaction", False):
            connection.exec_driver_sql("BEGIN")
    try:
        with db.begin_nested():
            row = InboundWebhookEvent(
                tenant_id=tenant_id,
                provider=provider,
                provider_event_id=event_id,
                event_type=notices[0].type if notices else "ignored",
                payload=payload,
            )
            db.add(row)
            db.flush()
            OutboxRepository(db).add(
                DomainEvent(
                    event_type="payment.provider_event",
                    aggregate_id=row.id,
                    tenant_id=tenant_id,
                    correlation_id=correlation_id,
                    payload={"inbound_event_id": str(row.id)},
                ),
                "payments",
            )
        return row.id
    except IntegrityError:
        existing = db.scalar(
            select(InboundWebhookEvent).where(
                InboundWebhookEvent.provider == provider,
                InboundWebhookEvent.tenant_id == tenant_id,
                InboundWebhookEvent.provider_event_id == event_id,
            )
        )
        if existing is None:
            raise
        return existing.id


def accept_webhook(
    db: Session,
    settings: Settings,
    provider: str,
    public_id: str,
    body: bytes,
    headers: dict[str, str],
    correlation_id: str | None,
) -> None:
    try:
        config = db.scalar(
            select(TenantPaymentConfig).where(
                TenantPaymentConfig.provider == provider,
                TenantPaymentConfig.webhook_public_id == public_id,
            )
        )
        if config is None or provider != "razorpay":
            raise NotFoundError("Webhook not found")
        secret = CredentialVault(settings.credentials_encryption_key).decrypt(config.webhook_secret_enc)
        try:
            notices = parse_signed(headers, body, secret)
        except ProviderDeclined:
            raise InvalidWebhook() from None
        stage_receipt(db, provider, config.tenant_id, headers["x-razorpay-event-id"], notices, correlation_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
