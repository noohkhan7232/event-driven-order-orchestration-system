from uuid import UUID

import pytest
from test_payment_config import identity
from test_payment_config import payment_settings as payment_settings


@pytest.fixture
def seller_endpoint(client, db, payment_settings, monkeypatch):
    from app.core import ssrf

    monkeypatch.setattr(ssrf, "resolve_addresses", lambda *args: ["8.8.8.8"])
    owner, auth = identity(db)
    response = client.post(
        "/api/v1/webhook-endpoints",
        headers=auth,
        json={
            "url": "https://seller.example/hook",
            "events": ["order.created", "order.paid", "order.status_changed", "payment.refunded", "ping"],
            "description": "Seller receiver",
        },
    )
    assert response.status_code == 201, response.text
    return owner, auth, response.json(), payment_settings


def test_webhook_secret_show_once_rotation_and_owner_crud(client, db, seller_endpoint, caplog):
    from app.models.entities import WebhookEndpoint

    _, auth, endpoint, _ = seller_endpoint
    secret = endpoint["signing_secret"]
    assert secret.startswith("whsec_")
    row = db.get(WebhookEndpoint, UUID(endpoint["id"]))
    assert secret not in row.secret_enc
    listed = client.get("/api/v1/webhook-endpoints", headers=auth)
    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert secret not in listed.text + caplog.text and "secret_enc" not in listed.text
    rotated = client.post(f"/api/v1/webhook-endpoints/{endpoint['id']}/rotate-secret", headers=auth)
    assert rotated.status_code == 200 and rotated.json()["signing_secret"] != secret
    assert client.delete(f"/api/v1/webhook-endpoints/{endpoint['id']}", headers=auth).status_code == 204
    assert client.get("/api/v1/webhook-endpoints", headers=auth).json()["total"] == 0


def test_ping_and_replay_preserve_event_identity(client, db, seller_endpoint):
    _, auth, endpoint, _ = seller_endpoint
    path = f"/api/v1/webhook-endpoints/{endpoint['id']}"
    ping = client.post(path + "/test", headers=auth)
    assert ping.status_code == 202
    delivery = client.get(path + "/deliveries", headers=auth).json()["items"][0]
    replay = client.post(path + f"/deliveries/{delivery['id']}/replay", headers=auth)
    assert replay.status_code == 200
    assert replay.json()["event_id"] == delivery["event_id"] and replay.json()["replay_count"] == 1


def test_status_fanout_is_unique_alias_safe_and_has_no_buyer_pii(db, workflow, seller_endpoint):
    from test_consumers import consume

    from app.events.contracts import EventNames
    from app.models.entities import OutboxEvent, WebhookDelivery
    from app.workers import tasks

    order, _ = workflow
    order.buyer_name, order.buyer_email, order.buyer_phone = "Private Buyer", "private@example.com", "+919876543210"
    order.external_reference = "seller-ref"
    db.commit()
    event = consume(order, EventNames.ORDER_CREATED)
    consume(order, EventNames.ORDER_CREATED, event)
    consume(order, EventNames.PAYMENT_REQUESTED)
    notices = db.query(OutboxEvent).filter_by(event_type="order.status_changed").all()
    for _ in range(2):
        for notice in notices:
            tasks.process_event.run(notice.payload)
    deliveries = db.query(WebhookDelivery).all()
    assert sum(row.event_type == "order.created" for row in deliveries) == 1
    assert sum(row.event_type == "order.paid" for row in deliveries) == 1
    assert len({row.event_id for row in deliveries}) == len(deliveries)
    for row in deliveries:
        text = str(row.payload)
        for forbidden in (
            "buyer_name",
            "buyer_email",
            "buyer_phone",
            "Private Buyer",
            "private@example.com",
            "+919876543210",
        ):
            assert forbidden not in text
        assert row.payload["data"]["external_reference"] == "seller-ref"


def test_owner_isolation_filters_validation_and_all_route_secret_sweep(client, db, seller_endpoint, caplog):
    from app.auth.principal import Principal
    from app.models.entities import Tenant
    from app.schemas.tenancy import ApiKeyCreate
    from app.services.tenancy import create_api_key

    owner, auth, endpoint, _ = seller_endpoint
    base = "/api/v1/webhook-endpoints"
    path = base + "/" + endpoint["id"]
    foreign = Tenant(name="Other receiver owner", slug="other-receiver")
    db.add(foreign)
    db.commit()
    _, other_auth = identity(db, tenant_id=foreign.id)
    delivery = client.post(path + "/test", headers=auth).json()
    _, token = create_api_key(
        db, Principal(owner.tenant_id, user=owner), ApiKeyCreate(name="Not an owner token", scopes=["orders:write"])
    )
    assert client.get(base, headers={"X-API-Key": token}).status_code == 403
    assert (
        client.post(base, headers={"X-API-Key": token}, json={"url": endpoint["url"], "events": ["ping"]}).status_code
        == 403
    )
    assert client.get(base, headers=other_auth).json()["total"] == 0
    for method, suffix, body in (
        ("PATCH", "", {"is_active": False}),
        ("DELETE", "", None),
        ("POST", "/test", None),
        ("POST", "/rotate-secret", None),
        ("GET", "/deliveries", None),
        ("POST", "/deliveries/" + delivery["id"] + "/replay", None),
    ):
        response = client.request(method, path + suffix, headers=other_auth, json=body)
        assert response.status_code == 404
    for body in ({"events": ["unknown"]}, {"events": []}, {"events": None}, {"url": None}, {"is_active": None}):
        assert client.patch(path, headers=auth, json=body).status_code == 422
    assert client.patch(path, headers=auth, json={"url": "http://127.0.0.1"}).status_code == 400
    assert client.post(base, headers=auth, json={"url": endpoint["url"], "events": ["unknown"]}).status_code == 422
    responses = [
        client.get(base, headers=auth),
        client.patch(
            path, headers=auth, json={"description": "Updated", "events": ["ping"], "url": "https://seller.example/new"}
        ),
        client.get(path + "/deliveries?status=pending&event_type=ping&limit=1&offset=0", headers=auth),
        client.post(path + "/deliveries/" + delivery["id"] + "/replay", headers=auth),
    ]
    assert all(response.status_code == 200 for response in responses)
    assert responses[2].json()["total"] == 1
    for response in responses:
        assert endpoint["signing_secret"] not in response.text and "secret_enc" not in response.text
    assert client.post(path + "/deliveries/" + str(UUID(int=1)) + "/replay", headers=auth).status_code == 404
    assert client.patch(path, headers=auth, json={"is_active": False}).status_code == 200
    assert client.post(path + "/test", headers=auth).status_code == 409
    assert client.post(path + "/deliveries/" + delivery["id"] + "/replay", headers=auth).status_code == 409
    assert endpoint["signing_secret"] not in caplog.text


def test_direct_duplicate_enqueue_refund_alias_and_foreign_fanout(db, workflow, seller_endpoint):
    from app.events.contracts import DomainEvent
    from app.models.entities import WebhookDelivery, WebhookEndpoint
    from app.services.outbound_webhooks import enqueue, fanout

    order, _ = workflow
    _, _, endpoint, _ = seller_endpoint
    row = db.get(WebhookEndpoint, UUID(endpoint["id"]))
    event = DomainEvent(event_type="refund.succeeded", aggregate_id=order.id, tenant_id=order.tenant_id, payload={})
    fanout(db, order, event)
    db.commit()
    delivery = db.query(WebhookDelivery).one()
    assert delivery.event_type == "payment.refunded"
    assert enqueue(db, row, delivery.event_id, delivery.event_type, delivery.payload).id == delivery.id
    fanout(db, order, event.model_copy(update={"tenant_id": UUID(int=42)}))
    fanout(db, order, event.model_copy(update={"event_type": "irrelevant"}))
    db.commit()
    assert db.query(WebhookDelivery).count() == 1


@pytest.mark.parametrize("role", ["customer", "vendor"])
def test_nonowner_jwt_cannot_manage_any_webhook_route(db, client, seller_endpoint, role):
    from app.models.enums import UserRole

    _, auth, endpoint, _ = seller_endpoint
    _, forbidden = identity(db, role=UserRole(role))
    base = "/api/v1/webhook-endpoints"
    path = base + "/" + endpoint["id"]
    delivery = client.post(path + "/test", headers=auth).json()
    requests = [
        ("POST", base, {"url": endpoint["url"], "events": ["ping"]}),
        ("GET", base, None),
        ("PATCH", path, {"is_active": False}),
        ("DELETE", path, None),
        ("POST", path + "/rotate-secret", None),
        ("POST", path + "/test", None),
        ("GET", path + "/deliveries", None),
        ("POST", path + "/deliveries/" + delivery["id"] + "/replay", None),
    ]
    for method, url, body in requests:
        assert client.request(method, url, headers=forbidden, json=body).status_code == 403


@pytest.mark.parametrize("state", ["paid", "shipped", "delivered", "failed", "cancelled"])
def test_alias_catalog_has_distinct_stable_ids_and_status_snapshot(db, client, workflow, seller_endpoint, state):
    from app.events.contracts import DomainEvent
    from app.models.entities import WebhookDelivery
    from app.services.outbound_webhooks import fanout

    order, _ = workflow
    _, auth, endpoint, _ = seller_endpoint
    assert (
        client.patch(
            "/api/v1/webhook-endpoints/" + endpoint["id"],
            headers=auth,
            json={"events": ["order.status_changed", "order." + state]},
        ).status_code
        == 200
    )
    event = DomainEvent(
        event_type="order.status_changed",
        aggregate_id=order.id,
        tenant_id=order.tenant_id,
        payload={"from": "created", "to": state},
    )
    for _ in range(2):
        fanout(db, order, event)
        db.commit()
    rows = db.query(WebhookDelivery).all()
    assert len(rows) == 2 and len({row.event_id for row in rows}) == 2
    assert {row.event_type for row in rows} == {"order.status_changed", "order." + state}
    assert all(row.payload["data"]["status"] == state and row.payload["data"]["to"] == state for row in rows)
