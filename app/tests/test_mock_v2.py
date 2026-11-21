from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.providers.contracts import PaymentAttempt, ProviderDeclined, ProviderUnavailable, SessionOrder


def test_mock_v2_signed_capture_deterministic_sessions_and_refund_retry():
    from app.providers.mock_v2 import MockPaymentProviderV2

    provider = MockPaymentProviderV2(Settings(_env_file=None, mock_refund_fail_attempts=1))
    order = SessionOrder(uuid4(), uuid4(), Decimal("12.00"), "INR", (), datetime.now(UTC))
    payment = PaymentAttempt(uuid4())
    session = provider.create_payment_session(order, payment, "same")
    assert session == provider.create_payment_session(order, payment, "same")
    headers, body = provider.signed_capture(session)
    (notice,) = provider.verify_and_parse_webhook(headers, body, provider.settings.mock_webhook_secret)
    assert notice.amount_minor == 1200 and notice.provider_order_id == session.provider_order_id
    assert provider.fetch_payment_status(session.provider_order_id) == [notice]
    assert MockPaymentProviderV2(provider.settings).fetch_payment_status(session.provider_order_id) == [notice]
    assert provider.fetch_payment_status("absent") == []
    with pytest.raises(ProviderUnavailable):
        provider.refund(payment, 1200, "same")
    result = provider.refund(payment, 1200, "same")
    assert result == provider.refund(payment, 1200, "same") == provider.fetch_refund(result.provider_refund_id)
    assert MockPaymentProviderV2(provider.settings).refund(payment, 1200, "same") == result
    for amount in [0, 100]:
        with pytest.raises(ProviderDeclined):
            provider.refund(payment, amount, "same")
    with pytest.raises(ProviderDeclined):
        provider.fetch_refund("absent")


def test_mock_auto_complete_forbidden_in_production():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="auto.complete"):
        Settings(
            _env_file=None,
            environment="production",
            secret_key="x" * 32,
            allow_mock_providers=True,
            mock_payment_auto_complete=True,
        )


@pytest.mark.parametrize("sku", ["FAIL-PAYMENT-X", "FAIL-TRANSIENT-X", "FAIL-TRANSIENT-EXHAUST-X"])
def test_mock_v2_preserves_failure_injection(sku):
    from app.providers.mock_v2 import MockPaymentProviderV2

    provider = MockPaymentProviderV2(Settings(_env_file=None, failure_injection_enabled=True))
    order = SessionOrder(uuid4(), uuid4(), Decimal("12"), "INR", (sku,), datetime.now(UTC))
    with pytest.raises(ProviderDeclined if sku.startswith("FAIL-PAYMENT") else ProviderUnavailable):
        provider.create_payment_session(order, PaymentAttempt(uuid4()), "key")
    if sku == "FAIL-TRANSIENT-X":
        assert provider.create_payment_session(order, PaymentAttempt(uuid4(), 2), "key")


def test_resolution_is_tenant_scoped_and_mock_requires_explicit_local_permission(db):
    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    from app.core.crypto import CredentialVault
    from app.models.entities import DEFAULT_TENANT_ID, TenantPaymentConfig
    from app.providers.resolution import PaymentNotConfigured, resolve_payment_provider

    settings = Settings(
        _env_file=None, allow_mock_providers=True, credentials_encryption_key=Fernet.generate_key().decode()
    )
    assert resolve_payment_provider(db, DEFAULT_TENANT_ID, settings).name == "mock"
    for changes in [{"allow_mock_providers": False}, {"environment": "production"}]:
        with pytest.raises(PaymentNotConfigured):
            resolve_payment_provider(db, DEFAULT_TENANT_ID, settings.model_copy(update=changes))
    token = CredentialVault(settings.credentials_encryption_key).encrypt(SecretStr("synthetic"))
    row = TenantPaymentConfig(
        tenant_id=DEFAULT_TENANT_ID,
        provider="razorpay",
        mode="test",
        key_id="public1234",
        key_secret_enc=token,
        webhook_secret_enc=token,
        webhook_public_id=uuid4().hex,
        is_active=True,
    )
    db.add(row)
    db.commit()
    assert resolve_payment_provider(db, DEFAULT_TENANT_ID, settings).name == "razorpay"
    assert resolve_payment_provider(db, uuid4(), settings).name == "mock"
    row.is_active = False
    db.commit()
    assert resolve_payment_provider(db, DEFAULT_TENANT_ID, settings).name == "mock"
