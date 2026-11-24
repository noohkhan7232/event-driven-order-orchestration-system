import asyncio
import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID

import httpx
import pytest
from conftest import TestingSessionLocal
from sqlalchemy import select
from test_outbound_webhooks import seller_endpoint as seller_endpoint
from test_payment_config import payment_settings as payment_settings
from test_webhook_delivery import ping

from app.models.entities import Tenant, WebhookDelivery, WebhookEndpoint
from app.services import webhook_delivery

pytestmark = pytest.mark.postgres


def test_postgres_webhook_skip_locked_and_http_holds_no_tenant_endpoint_delivery_locks(
    db, client, seller_endpoint, monkeypatch
):
    owner, auth, endpoint, settings = seller_endpoint
    identity = ping(client, auth, endpoint)
    claiming, commit_release, sending, send_release = Event(), Event(), Event(), Event()
    original_commit = TestingSessionLocal.class_.commit
    original_client = httpx.AsyncClient
    calls = []

    def before_commit(session):
        if not claiming.is_set() and any(isinstance(row, WebhookDelivery) and row.claim_token for row in session.dirty):
            claiming.set()
            assert commit_release.wait(15)
        return original_commit(session)

    async def receiver(request):
        assert request.url.host == "8.8.8.8"
        assert request.headers["Host"] == request.extensions["sni_hostname"] == "seller.example"
        body = request.content
        expected = hmac.new(
            endpoint["signing_secret"].encode(),
            request.headers["X-OOS-Timestamp"].encode() + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["X-OOS-Signature"] == "t=" + request.headers["X-OOS-Timestamp"] + ",v1=" + expected
        calls.append(request.headers["X-OOS-Event-Id"])
        sending.set()
        assert await asyncio.to_thread(send_release.wait, 15)
        return httpx.Response(204)

    monkeypatch.setattr(TestingSessionLocal.class_, "commit", before_commit)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(receiver), **kwargs)
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(webhook_delivery.run_delivery_batch, TestingSessionLocal, settings)
        try:
            assert claiming.wait(15)
            assert webhook_delivery.run_delivery_batch(TestingSessionLocal, settings) == {}
            commit_release.set()
            assert sending.wait(15)
            with TestingSessionLocal() as observer:
                for column, key in (
                    (Tenant.id, owner.tenant_id),
                    (WebhookEndpoint.id, UUID(endpoint["id"])),
                    (WebhookDelivery.id, identity),
                ):
                    assert observer.scalar(select(column).where(column == key).with_for_update(nowait=True)) == key
            assert webhook_delivery.run_delivery_batch(TestingSessionLocal, settings) == {}
            # An owner write also completes during the actual HTTPX transport call.
            assert (
                client.post(f"/api/v1/webhook-endpoints/{endpoint['id']}/rotate-secret", headers=auth).status_code
                == 200
            )
        finally:
            commit_release.set()
            send_release.set()
        assert running.result(timeout=15) == {"delivered": 1}
    db.expire_all()
    delivery = db.get(WebhookDelivery, identity)
    assert delivery.status == "delivered" and delivery.attempts == 1
    assert calls == [str(delivery.event_id)]
    print(
        "OUTBOUND HTTP IN FLIGHT: TENANT/ENDPOINT/DELIVERY NOWAIT AND OWNER ROTATION PASS; PEER SKIP LOCKED; ONE SEND"
    )
