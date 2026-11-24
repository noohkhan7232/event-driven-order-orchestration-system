import hashlib
import hmac
from datetime import timedelta
from uuid import UUID

import pytest
from conftest import TestingSessionLocal
from test_outbound_webhooks import seller_endpoint as seller_endpoint
from test_payment_config import payment_settings as payment_settings

from app.auth.principal import aware
from app.models.entities import Tenant, WebhookDelivery, WebhookEndpoint
from app.services.payment_timing import database_now


def ping(client, auth, endpoint):
    response = client.post(f"/api/v1/webhook-endpoints/{endpoint['id']}/test", headers=auth)
    assert response.status_code == 202
    return UUID(response.json()["id"])


def due(db, identity):
    row = db.get(WebhookDelivery, identity)
    row.next_attempt_at = database_now(db) - timedelta(seconds=1)
    db.commit()
    return row


def test_independent_signature_retry_dead_and_same_event_replay(db, client, seller_endpoint, monkeypatch, caplog):
    from app.services import webhook_delivery

    _, auth, endpoint, settings = seller_endpoint
    settings.webhook_max_attempts = 2
    identity = ping(client, auth, endpoint)
    calls = []
    responses = iter([503, 503, 204])

    def send(url, body, headers, settings):
        assert not db.in_transaction()
        signature = hmac.new(
            endpoint["signing_secret"].encode(), headers["X-OOS-Timestamp"].encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        assert headers["X-OOS-Signature"] == "t=" + headers["X-OOS-Timestamp"] + ",v1=" + signature
        assert headers["User-Agent"] == "oos-webhooks/1" and headers["Content-Type"] == "application/json"
        calls.append((body, headers["X-OOS-Event-Id"], headers["X-OOS-Delivery-Id"]))
        return next(responses)

    monkeypatch.setattr(webhook_delivery, "send_webhook", send)
    webhook_delivery.run_delivery_batch(TestingSessionLocal, settings)
    db.expire_all()
    row = db.get(WebhookDelivery, identity)
    assert row.status == "failed" and row.attempts == 1 and row.last_status_code == 503
    assert aware(row.next_attempt_at) > database_now(db)
    db.commit()
    webhook_delivery.run_delivery_batch(TestingSessionLocal, settings)
    assert len(calls) == 1
    due(db, identity)
    webhook_delivery.run_delivery_batch(TestingSessionLocal, settings)
    db.expire_all()
    assert row.status == "dead" and row.attempts == 2
    replay = client.post(f"/api/v1/webhook-endpoints/{endpoint['id']}/deliveries/{identity}/replay", headers=auth)
    assert replay.status_code == 200 and replay.json()["replay_count"] == 1
    webhook_delivery.run_delivery_batch(TestingSessionLocal, settings)
    db.expire_all()
    assert row.status == "delivered" and row.delivered_at is not None
    db.commit()
    webhook_delivery.run_delivery_batch(TestingSessionLocal, settings)
    assert len(calls) == 3 and len(set(calls)) == 1
    assert endpoint["signing_secret"] not in caplog.text


def test_auto_disable_reenable_suspension_and_dns_recheck(db, client, seller_endpoint, monkeypatch):
    from app.core import ssrf
    from app.services import webhook_delivery

    owner, auth, endpoint, settings = seller_endpoint
    settings.webhook_disable_after_failures = 1
    identity = ping(client, auth, endpoint)
    monkeypatch.setattr(ssrf, "resolve_addresses", lambda *args: ["169.254.169.254"])
    webhook_delivery.run_delivery_batch(TestingSessionLocal, settings)
    db.expire_all()
    row = db.get(WebhookDelivery, identity)
    assert row.last_error == "unsafe_target"
    config = db.get(WebhookEndpoint, UUID(endpoint["id"]))
    assert not config.is_active and config.disabled_at is not None
    assert (
        client.patch(f"/api/v1/webhook-endpoints/{config.id}", headers=auth, json={"is_active": True}).status_code
        == 200
    )
    assert config.consecutive_failures == 0 and config.disabled_at is None
    tenant = db.get(Tenant, owner.tenant_id)
    tenant.status = "suspended"
    db.commit()
    monkeypatch.setattr(webhook_delivery, "send_webhook", lambda *a: pytest.fail("Suspended tenant sent HTTP"))
    due(db, identity)
    webhook_delivery.run_delivery_batch(TestingSessionLocal, settings)
    db.expire_all()
    assert row.attempts == 1


def test_http_redirects_not_followed_response_stream_capped_and_errors_sanitized(
    db, client, seller_endpoint, monkeypatch
):
    import httpx

    from app.services import webhook_delivery

    _, auth, endpoint, settings = seller_endpoint
    identity = ping(client, auth, endpoint)
    original = httpx.AsyncClient
    calls = []
    chunks = []

    class LargeStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                chunks.append(1)
                yield b"x" * 1024

    def handler(request):
        assert request.url.host == "8.8.8.8"
        assert request.headers["Host"] == "seller.example"
        assert request.extensions["sni_hostname"] == "seller.example"
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/unsafe"}, stream=LargeStream())

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
    )
    webhook_delivery.run_delivery_batch(TestingSessionLocal, settings)
    db.expire_all()
    row = db.get(WebhookDelivery, identity)
    assert row.last_status_code == 302 and row.last_error == "http_status" and row.status == "failed"
    assert calls == ["https://8.8.8.8/hook"] and len(chunks) == 1
    due(db, identity)
    monkeypatch.setattr(
        webhook_delivery,
        "send_webhook",
        lambda *a: (_ for _ in ()).throw(httpx.ConnectError("private body and signing secret")),
    )
    webhook_delivery.run_delivery_batch(TestingSessionLocal, settings)
    db.expire_all()
    assert row.last_error == "transport_error"


def test_crash_leases_expire_budget_is_durable_stale_finish_and_inflight_replay(db, client, seller_endpoint):
    from app.services import webhook_delivery

    _, auth, endpoint, settings = seller_endpoint
    settings.webhook_max_attempts = 1
    identity = ping(client, auth, endpoint)
    first = webhook_delivery.claim_delivery(TestingSessionLocal, settings)
    assert first and webhook_delivery.claim_delivery(TestingSessionLocal, settings) is None
    assert (
        client.post(
            f"/api/v1/webhook-endpoints/{endpoint['id']}/deliveries/{identity}/replay", headers=auth
        ).status_code
        == 409
    )
    db.expire_all()
    row = db.get(WebhookDelivery, identity)
    row.lease_until = database_now(db) - timedelta(seconds=1)
    db.commit()
    assert webhook_delivery.run_delivery_batch(TestingSessionLocal, settings) == {"dead": 1}
    assert webhook_delivery.finish_delivery(TestingSessionLocal, settings, first, 200, None) == "stale"
    db.expire_all()
    assert row.status == "dead" and row.attempts == 1


def test_unusable_encryption_key_never_sends_or_leaks(db, client, seller_endpoint, monkeypatch):
    from app.services import webhook_delivery

    _, auth, endpoint, settings = seller_endpoint
    identity = ping(client, auth, endpoint)
    settings.credentials_encryption_key = None
    monkeypatch.setattr(webhook_delivery, "send_webhook", lambda *a: pytest.fail("No usable credentials"))
    webhook_delivery.run_delivery_batch(TestingSessionLocal, settings)
    db.expire_all()
    assert db.get(WebhookDelivery, identity).last_error == "credentials_unavailable"
