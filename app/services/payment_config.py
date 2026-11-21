import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.principal import Principal
from app.core.config import Settings
from app.core.crypto import CredentialVault
from app.core.exceptions import NotFoundError
from app.models.entities import Tenant, TenantPaymentConfig
from app.schemas.payment_config import PaymentConfigRead, PaymentConfigWrite


def owned_config(db: Session, principal: Principal, *, lock: bool = False) -> TenantPaymentConfig:
    principal.require_owner()
    query = select(TenantPaymentConfig).where(
        TenantPaymentConfig.tenant_id == principal.tenant_id, TenantPaymentConfig.provider == "razorpay"
    )
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        raise NotFoundError("Payment configuration not found")
    return row


def masked_config(row: TenantPaymentConfig, settings: Settings) -> PaymentConfigRead:
    return PaymentConfigRead(
        key_id="****" + row.key_id[-4:],
        webhook_url=(
            str(settings.public_base_url).rstrip("/") + "/api/v1/webhooks/payments/razorpay/" + row.webhook_public_id
        ),
        webhook_public_id=row.webhook_public_id,
        mode=row.mode,
        active=row.is_active,
    )


def save_config(
    db: Session, principal: Principal, payload: PaymentConfigWrite, settings: Settings
) -> TenantPaymentConfig:
    principal.require_owner()
    vault = CredentialVault(settings.credentials_encryption_key)
    key_secret, webhook_secret = vault.encrypt(payload.key_secret), vault.encrypt(payload.webhook_secret)
    try:
        # Serialize first insert and updates per tenant, including the absent-row case.
        db.scalar(select(Tenant).where(Tenant.id == principal.tenant_id).with_for_update())
        row = db.scalar(
            select(TenantPaymentConfig).where(
                TenantPaymentConfig.tenant_id == principal.tenant_id, TenantPaymentConfig.provider == "razorpay"
            )
        )
        if row is None:
            row = TenantPaymentConfig(
                tenant_id=principal.tenant_id, provider="razorpay", webhook_public_id=secrets.token_urlsafe(32)
            )
            db.add(row)
        row.key_id, row.key_secret_enc, row.webhook_secret_enc = payload.key_id, key_secret, webhook_secret
        row.mode, row.is_active = payload.mode, True
        db.commit()
        return row
    except Exception:
        db.rollback()
        raise
