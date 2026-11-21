from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.crypto import CredentialVault
from app.core.exceptions import DomainError
from app.models.entities import TenantPaymentConfig
from app.providers.contracts import PaymentProviderV2
from app.providers.mock_v2 import MockPaymentProviderV2
from app.providers.razorpay import RazorpayProvider


class PaymentNotConfigured(DomainError):
    code = "payment_not_configured"
    status_code = 422


def resolve_payment_provider(db: Session, tenant_id: UUID, settings: Settings | None = None) -> PaymentProviderV2:
    settings = settings or get_settings()
    row = db.scalar(
        select(TenantPaymentConfig).where(
            TenantPaymentConfig.tenant_id == tenant_id,
            TenantPaymentConfig.provider == "razorpay",
            TenantPaymentConfig.is_active.is_(True),
        )
    )
    if row:
        secret = CredentialVault(settings.credentials_encryption_key).decrypt(row.key_secret_enc)
        return RazorpayProvider(row.key_id, secret)
    if settings.environment in {"local", "development", "test"} and settings.allow_mock_providers:
        return MockPaymentProviderV2(settings)
    raise PaymentNotConfigured("Prepaid payments are not configured")


def resolve_refund_provider(
    db: Session, tenant_id: UUID, provider: str | None, settings: Settings | None = None
) -> PaymentProviderV2:
    settings = settings or get_settings()
    if provider in {"mock", "simulation_gateway"}:
        if settings.environment in {"local", "development", "test"} and settings.allow_mock_providers:
            return MockPaymentProviderV2(settings)
    elif provider == "razorpay":
        row = db.scalar(
            select(TenantPaymentConfig).where(
                TenantPaymentConfig.tenant_id == tenant_id, TenantPaymentConfig.provider == provider
            )
        )
        if row:
            return RazorpayProvider(
                row.key_id, CredentialVault(settings.credentials_encryption_key).decrypt(row.key_secret_enc)
            )
    raise PaymentNotConfigured("Refund provider is not configured")
