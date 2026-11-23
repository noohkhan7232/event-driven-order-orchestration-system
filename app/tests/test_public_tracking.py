import base64
import hashlib
import hmac
from uuid import uuid4

import pytest
from test_payment_config import identity

from app.core.config import get_settings
from app.models.entities import OrderTimeline, Shipment, ShipmentEvent, Tenant
from app.models.enums import ShipmentStatus


def test_tracking_token_contract_public_allowlist_rotation_and_headers(db, client, workflow, caplog):
    order, _ = workflow
    _, auth = identity(db)
    order.buyer_name, order.buyer_email, order.buyer_phone = "Private buyer", "private@example.com", "+919876543210"
    order.failure_reason = "PRIVATE_FAILURE"
    db.add(OrderTimeline(tenant_id=order.tenant_id, order_id=order.id, to_status="failed", reason="PRIVATE_REASON"))
    shipment = Shipment(
        order_id=order.id, carrier="Courier", tracking_number="TRACK", tracking_url="https://example.com/track"
    )
    db.add(shipment)
    db.flush()
    db.add(
        ShipmentEvent(
            shipment_id=shipment.id, status=ShipmentStatus.DELAYED, notes="PRIVATE_NOTES", location="PRIVATE_LOCATION"
        )
    )
    db.commit()
    owned = client.get(f"/api/v1/orders/{order.id}", headers=auth).json()
    token = owned["tracking_token"]
    expected = (
        base64.urlsafe_b64encode(
            hmac.new(
                get_settings().tracking_token_secret.get_secret_value().encode(),
                f"{order.id}:1".encode(),
                hashlib.sha256,
            ).digest()
        )
        .decode()
        .rstrip("=")
    )
    assert token == order.id.hex + "." + expected and owned["tracking_url"].endswith("/api/v1/public/track/" + token)
    response = client.get("/api/v1/public/track/" + token)
    assert (
        response.status_code == 200
        and response.headers["cache-control"] == "no-store"
        and response.headers["referrer-policy"] == "no-referrer"
    )
    for private in (
        "buyer_name",
        "buyer_email",
        "buyer_phone",
        "Private buyer",
        "private@example.com",
        "PRIVATE_FAILURE",
        "PRIVATE_REASON",
        "PRIVATE_NOTES",
        "PRIVATE_LOCATION",
        "payments",
        "total",
        "unit_price",
        "customer_id",
    ):
        assert private not in response.text
    assert response.json()["shipments"][0]["tracking_number"] == "TRACK"
    rotated = client.post(f"/api/v1/orders/{order.id}/tracking/rotate", headers=auth)
    assert rotated.status_code == 200 and rotated.json()["tracking_token"] != token
    old = client.get("/api/v1/public/track/" + token)
    invalid = client.get("/api/v1/public/track/invalid")
    assert old.status_code == invalid.status_code == 404 and old.json() == invalid.json()
    assert client.get("/api/v1/public/track/" + rotated.json()["tracking_token"]).status_code == 200
    http_logs = [record.getMessage() for record in caplog.records if record.name == "http"]
    assert all(token not in line for line in http_logs)


def test_foreign_rotation_suspend_rate_limit_and_optional_total(db, client, workflow, monkeypatch):
    order, _ = workflow
    _, auth = identity(db)
    foreign = Tenant(name="Foreign", slug="foreign-tracking")
    db.add(foreign)
    db.commit()
    _, other = identity(db, tenant_id=foreign.id)
    assert client.post(f"/api/v1/orders/{order.id}/tracking/rotate", headers=other).status_code == 404
    token = client.get(f"/api/v1/orders/{order.id}", headers=auth).json()["tracking_token"]
    settings = get_settings()
    monkeypatch.setattr(settings, "public_tracking_show_total", True)
    assert client.get("/api/v1/public/track/" + token).json()["total"] == "10.00"
    monkeypatch.setattr(settings, "rate_limit_tracking_limit", 2)
    from app.core.ratelimit import get_limiter

    get_limiter.cache_clear()
    assert client.get("/api/v1/public/track/" + token).status_code == 200
    assert client.get("/api/v1/public/track/bad").status_code == 404
    limited = client.get("/api/v1/public/track/" + token)
    assert (
        limited.status_code == 429
        and int(limited.headers["retry-after"]) > 0
        and limited.headers["cache-control"] == "no-store"
    )
    get_limiter.cache_clear()
    db.get(Tenant, order.tenant_id).status = "suspended"
    db.commit()
    assert client.get("/api/v1/public/track/" + token).status_code == 404


def test_invalid_unknown_tokens_use_constant_time_compare(db, client, workflow, monkeypatch):
    import app.services.tracking as tracking

    original = hmac.compare_digest
    calls = []
    monkeypatch.setattr(
        tracking.hmac, "compare_digest", lambda a, b: (calls.append((len(a), len(b))), original(a, b))[1]
    )
    for token in ("bad", uuid4().hex + "." + "a" * 43, workflow[0].id.hex + "." + "b" * 43, "x" * 200):
        response = client.get("/api/v1/public/track/" + token)
        assert response.status_code == 404 and response.headers["referrer-policy"] == "no-referrer"
    assert len(calls) == 4 and all(left == right == 43 for left, right in calls)


@pytest.mark.parametrize(
    "state,friendly",
    [
        ("created", "confirmed"),
        ("validated", "confirmed"),
        ("inventory_reserved", "processing"),
        ("payment_pending", "processing"),
        ("paid", "processing"),
        ("fulfillment_requested", "processing"),
        ("shipped", "shipped"),
        ("delivered", "delivered"),
        ("cancelled", "cancelled"),
        ("failed", "failed"),
    ],
)
def test_public_status_mapping_and_private_timeline_fallback(db, client, workflow, state, friendly):
    from app.models.enums import OrderStatus

    order, _ = workflow
    order.status = OrderStatus(state)
    db.add(
        OrderTimeline(tenant_id=order.tenant_id, order_id=order.id, to_status="future_internal_state", reason="private")
    )
    db.commit()
    response = client.get("/api/v1/public/track/" + order.tracking_token)
    assert response.status_code == 200 and response.json()["status"] == friendly
    assert "future_internal_state" not in response.text and "private" not in response.text


def test_rotation_requires_staff_or_write_key_and_tracking_secret_is_validated(db, client, workflow):
    from cryptography.fernet import Fernet
    from pydantic import ValidationError

    from app.auth.principal import Principal
    from app.auth.security import create_access_token
    from app.core.config import Settings
    from app.schemas.tenancy import ApiKeyCreate
    from app.services.tenancy import create_api_key

    order, _ = workflow
    owner, auth = identity(db)
    url = f"/api/v1/orders/{order.id}/tracking/rotate"
    assert (
        client.post(url, headers={"Authorization": "Bearer " + create_access_token(str(order.customer_id))}).status_code
        == 403
    )
    for scopes, expected in ((["orders:read"], 403), (["orders:write"], 200)):
        _, key = create_api_key(
            db, Principal(owner.tenant_id, user=owner), ApiKeyCreate(name="tracking", scopes=scopes)
        )
        assert client.post(url, headers={"X-API-Key": key}).status_code == expected
    with pytest.raises(ValidationError, match="TRACKING_TOKEN_SECRET"):
        Settings(
            _env_file=None,
            environment="production",
            secret_key="s" * 32,
            shipping_provider="manual",
            allow_mock_providers=True,
            credentials_encryption_key=Fernet.generate_key().decode(),
            notifications_allow_console=True,
        )
    with pytest.raises(ValidationError):
        Settings(_env_file=None, tracking_token_secret="")


def test_public_tracking_limiter_outage_keeps_privacy_headers(client, monkeypatch):
    from app.core import ratelimit

    class Broken:
        def hit(self, *args):
            raise ratelimit.BackendUnavailable()

    monkeypatch.setattr(ratelimit, "get_limiter", lambda *args: Broken())
    response = client.get("/api/v1/public/track/bad")
    assert (
        response.status_code == 503
        and response.headers["cache-control"] == "no-store"
        and response.headers["referrer-policy"] == "no-referrer"
    )
