import hashlib
import hmac
import json
from uuid import uuid4

import pytest
from test_payment_config import identity
from test_payment_config import payment_settings as payment_settings

from app.core.config import get_settings
from app.main import app


def signed(event_id="evt_one", event="payment.captured", payment_id="pay_one", order_id="order_one", amount=100):
    body = json.dumps(
        {
            "event": event,
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": order_id,
                        "amount": amount,
                        "currency": "INR",
                        "email": "private-buyer@example.com",
                        "notes": {"secret": "synthetic-private-data"},
                    }
                }
            },
        }
    ).encode()
    return body, {
        "X-Razorpay-Signature": hmac.new(b"synthetic-hook", body, hashlib.sha256).hexdigest(),
        "X-Razorpay-Event-Id": event_id,
    }


@pytest.fixture
def webhook(client, db, payment_settings):
    owner, auth = identity(db)
    response = client.put(
        "/api/v1/settings/payments/razorpay",
        headers=auth,
        json={
            "key_id": "public1234",
            "key_secret": "synthetic-api",
            "webhook_secret": "synthetic-hook",
            "mode": "test",
        },
    )
    assert response.status_code == 200
    return "/api/v1/webhooks/payments/razorpay/" + response.json()["webhook_public_id"], owner.tenant_id


def test_inbound_receipt_and_intent_are_durable_sanitized_and_deduplicated(client, db, webhook, caplog):
    from app.models.entities import InboundWebhookEvent, OutboxEvent

    path, tenant = webhook
    body, headers = signed()
    for _ in range(2):
        response = client.post(path, content=body, headers=headers)
        assert response.status_code == 200 and response.json() == {"accepted": True}
    row = db.query(InboundWebhookEvent).one()
    intent = db.query(OutboxEvent).filter_by(event_type="payment.provider_event").one()
    assert row.tenant_id == tenant == intent.tenant_id and row.provider_event_id == "evt_one"
    assert row.status == "received" and row.event_type == "payment_captured"
    assert intent.aggregate_id == row.id and intent.queue == "payments"
    assert "private-buyer" not in repr(row.payload) + caplog.text
    assert "synthetic-private-data" not in repr(row.payload) + caplog.text
    assert "synthetic-hook" not in response.text + caplog.text


def test_deactivated_configuration_still_accepts_verified_financial_callbacks(client, db, webhook):
    from app.models.entities import TenantPaymentConfig

    row = db.query(TenantPaymentConfig).filter_by(tenant_id=webhook[1]).one()
    row.is_active = False
    db.commit()
    body, headers = signed()
    assert client.post(webhook[0], content=body, headers=headers).status_code == 200


def test_two_tenants_accept_the_same_event_id_without_receipt_or_secret_leakage(client, db, webhook):
    from app.models.entities import InboundWebhookEvent, OutboxEvent, Tenant

    tenant = Tenant(name="Independent webhook seller", slug="independent-hook")
    db.add(tenant)
    db.commit()
    _, auth = identity(db, tenant_id=tenant.id)
    config = client.put(
        "/api/v1/settings/payments/razorpay",
        headers=auth,
        json={
            "key_id": "independent1234",
            "key_secret": "synthetic-api",
            "webhook_secret": "synthetic-hook",
            "mode": "test",
        },
    )
    assert config.status_code == 200
    second_path = "/api/v1/webhooks/payments/razorpay/" + config.json()["webhook_public_id"]
    body, headers = signed(event_id="evt_shared")
    for path in (webhook[0], second_path):
        for _ in range(2):
            response = client.post(path, content=body, headers=headers)
            assert response.status_code == 200 and response.json() == {"accepted": True}
            for private in ("synthetic-api", "synthetic-hook", "private-buyer", "synthetic-private-data"):
                assert private not in response.text
    assert db.query(InboundWebhookEvent).count() == 2
    intents = db.query(OutboxEvent).filter_by(event_type="payment.provider_event").all()
    assert len(intents) == 2 and {row.tenant_id for row in intents} == {webhook[1], tenant.id}
    assert "private-buyer" not in repr([row.payload for row in db.query(InboundWebhookEvent)])


@pytest.mark.parametrize("case", ["missing", "wrong", "changed", "event_missing", "malformed", "oversized"])
def test_inbound_bad_signatures_and_bodies_persist_nothing(client, db, webhook, case):
    from app.models.entities import InboundWebhookEvent, OutboxEvent

    body, headers = signed()
    if case == "missing":
        headers.pop("X-Razorpay-Signature")
    if case == "wrong":
        headers["X-Razorpay-Signature"] = "0" * 64
    if case == "changed":
        body += b" "
    if case == "event_missing":
        headers.pop("X-Razorpay-Event-Id")
    if case == "malformed":
        body = b"{"
        headers["X-Razorpay-Signature"] = hmac.new(b"synthetic-hook", body, hashlib.sha256).hexdigest()
    if case == "oversized":
        body = b"x" * 65537
    response = client.post(webhook[0], content=body, headers=headers)
    assert response.status_code == (413 if case == "oversized" else 401)
    assert db.query(InboundWebhookEvent).count() == 0
    assert db.query(OutboxEvent).filter_by(event_type="payment.provider_event").count() == 0


def test_inbound_unknown_route_and_quota(client, db, webhook, payment_settings):
    body, headers = signed()
    assert (
        client.post("/api/v1/webhooks/payments/razorpay/" + uuid4().hex, content=body, headers=headers).status_code
        == 404
    )
    assert client.post(webhook[0].replace("/razorpay/", "/unknown/"), content=body, headers=headers).status_code == 404
    app.dependency_overrides[get_settings] = lambda: payment_settings.model_copy(update={"rate_limit_webhook_limit": 1})
    from app.core.ratelimit import get_limiter

    get_limiter.cache_clear()
    assert client.post(webhook[0], content=body, headers=headers).status_code == 200
    limited = client.post(webhook[0], content=body, headers=headers)
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0


def test_inbound_outbox_failure_rolls_back_receipt(client, db, webhook, monkeypatch):
    from app.models.entities import InboundWebhookEvent
    from app.repositories.outbox import OutboxRepository

    body, headers = signed()
    monkeypatch.setattr(OutboxRepository, "add", lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
    with pytest.raises(RuntimeError, match="synthetic failure"):
        client.post(webhook[0], content=body, headers=headers)
    assert db.query(InboundWebhookEvent).count() == 0


@pytest.mark.postgres
def test_postgres_concurrent_inbound_receipts_have_one_intent(db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from conftest import TestingSessionLocal
    from pydantic import SecretStr

    from app.models.entities import DEFAULT_TENANT_ID, InboundWebhookEvent, OutboxEvent
    from app.providers.razorpay import parse_signed
    from app.services.inbound_payments import stage_receipt

    body, headers = signed(event_id="evt_concurrent")
    notices = parse_signed(headers, body, SecretStr("synthetic-hook"))
    barrier = Barrier(2)

    def deliver(_):
        with TestingSessionLocal() as session:
            barrier.wait(timeout=10)
            identity = stage_receipt(session, "razorpay", DEFAULT_TENANT_ID, "evt_concurrent", notices)
            session.commit()
            return identity

    db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        identities = list(pool.map(deliver, range(2)))
    assert identities[0] == identities[1]
    assert db.query(InboundWebhookEvent).count() == 1
    assert db.query(OutboxEvent).filter_by(event_type="payment.provider_event").count() == 1
    print("CONCURRENT VERIFIED RECEIPTS: ONE JOURNAL ROW AND ONE INTENT")
