from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from app.auth.principal import Principal
from app.auth.security import create_access_token
from app.core.config import Settings, get_settings
from app.main import app
from app.models.entities import DEFAULT_TENANT_ID, Tenant, User
from app.models.enums import UserRole
from app.schemas.tenancy import ApiKeyCreate
from app.services.tenancy import create_api_key

PATH = "/api/v1/settings/payments/razorpay"


def test_crypto_roundtrip_rotation_and_safe_errors():
    from app.core.crypto import CredentialsUnavailable, CredentialVault

    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    vault = CredentialVault(SecretStr(old))
    encrypted = vault.encrypt(SecretStr("synthetic-credential"))
    assert "synthetic-credential" not in encrypted
    assert vault.decrypt(encrypted).get_secret_value() == "synthetic-credential"
    ring = CredentialVault(SecretStr(new + "," + old))
    rotated = ring.rotate(encrypted)
    assert CredentialVault(SecretStr(new)).decrypt(rotated).get_secret_value() == "synthetic-credential"
    for unusable in [CredentialVault(None), vault]:
        with pytest.raises(CredentialsUnavailable):
            unusable.rotate("invalid")
    with pytest.raises(CredentialsUnavailable):
        CredentialVault(None).encrypt(SecretStr("synthetic-credential"))
    for bad_vault, token in [(vault, rotated), (vault, "invalid"), (CredentialVault(None), encrypted)]:
        with pytest.raises(CredentialsUnavailable) as error:
            bad_vault.decrypt(token)
        assert str(error.value) == "Payment credentials unavailable"


def test_encryption_settings_required_and_validated():
    for key in [None, "invalid-key"]:
        with pytest.raises(ValidationError, match="CREDENTIALS_ENCRYPTION_KEY") as error:
            Settings(
                _env_file=None,
                environment="production",
                secret_key="x" * 32,
                allow_mock_providers=True,
                credentials_encryption_key=key,
            )
        assert "invalid-key" not in str(error.value)
    assert Settings(_env_file=None, credentials_encryption_key=Fernet.generate_key().decode())


@pytest.fixture
def payment_settings(db):
    settings = get_settings().model_copy(
        update={"credentials_encryption_key": SecretStr(Fernet.generate_key().decode())}
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


def identity(db, role=UserRole.ADMIN, tenant_id=DEFAULT_TENANT_ID):
    user = User(
        tenant_id=tenant_id, email=f"{uuid4().hex}@example.com", full_name="Test", hashed_password="unused", role=role
    )
    db.add(user)
    db.commit()
    return user, {"Authorization": "Bearer " + create_access_token(str(user.id))}


def test_owner_payment_settings_mask_encrypt_isolate_and_deactivate(client, db, payment_settings, caplog):
    from app.core.crypto import CredentialVault
    from app.models.entities import TenantPaymentConfig

    owner, headers = identity(db)
    payload = {
        "key_id": "rzp_test_public1234",
        "key_secret": "synthetic-payment-secret",
        "webhook_secret": "synthetic-webhook-secret",
        "mode": "test",
    }
    response = client.put(PATH, json=payload, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["key_id"] == "****1234" and body["active"] and body["mode"] == "test"
    assert body["webhook_url"].endswith("/razorpay/" + body["webhook_public_id"])
    row = db.scalar(select(TenantPaymentConfig).where(TenantPaymentConfig.tenant_id == owner.tenant_id))
    for field in ["key_secret", "webhook_secret"]:
        token = getattr(row, field + "_enc")
        assert token != payload[field] and payload[field] not in token
        assert (
            CredentialVault(payment_settings.credentials_encryption_key).decrypt(token).get_secret_value()
            == payload[field]
        )
        assert payload[field] not in response.text + caplog.text
    other = Tenant(name="Other", slug="other-payments")
    db.add(other)
    db.commit()
    _, other_headers = identity(db, tenant_id=other.id)
    assert client.get(PATH, headers=other_headers).status_code == 404
    assert client.delete(PATH, headers=other_headers).status_code == 404
    payload["key_secret"] = "synthetic-rotated-secret"
    updated = client.put(PATH, json=payload, headers=headers)
    assert updated.json()["webhook_public_id"] == body["webhook_public_id"]
    assert db.query(TenantPaymentConfig).count() == 1
    assert client.get(PATH, headers=headers).json() == updated.json()
    assert client.delete(PATH, headers=headers).status_code == 204
    assert not client.get(PATH, headers=headers).json()["active"]
    assert client.delete(PATH, headers=headers).status_code == 204


@pytest.mark.parametrize("role", ["vendor", "customer", "key", "platform"])
def test_payment_settings_require_tenant_owner_jwt(client, db, role, payment_settings):
    user, headers = identity(db, UserRole.ADMIN if role in {"key", "platform"} else UserRole(role))
    if role == "key":
        _, key = create_api_key(
            db, Principal(user.tenant_id, user=user), ApiKeyCreate(name="test", scopes=["orders:write"])
        )
        headers = {"X-API-Key": key}
    if role == "platform":
        user.tenant_id, user.is_platform_admin = None, True
        db.commit()
    for method in ["get", "put", "delete"]:
        response = getattr(client, method)(
            PATH,
            headers=headers,
            **(
                {
                    "json": {
                        "key_id": "public1234",
                        "key_secret": "synthetic-secret",
                        "webhook_secret": "synthetic-hook",
                        "mode": "test",
                    }
                }
                if method == "put"
                else {}
            ),
        )
        assert response.status_code == 403


def test_payment_settings_validation_and_missing_encryption_are_safe(client, db):
    _, headers = identity(db)
    body = {
        "key_id": "public1234",
        "key_secret": "synthetic-secret",
        "webhook_secret": "synthetic-hook",
        "mode": "wrong",
    }
    invalid = client.put(PATH, headers=headers, json=body)
    assert invalid.status_code == 422 and "synthetic-secret" not in invalid.text
    body["mode"] = "test"
    response = client.put(PATH, headers=headers, json=body)
    assert response.status_code == 503 and response.json()["code"] == "credentials_unavailable"
    assert "synthetic-secret" not in response.text
