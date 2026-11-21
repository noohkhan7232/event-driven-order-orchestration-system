from conftest import auth_header

from app.models.entities import Order, OutboxEvent
from app.repositories.orders import OrderRepository


def test_signup_email_normalizes_before_duplicate_check(client):
    payload = {
        "tenant_slug": "default",
        "email": "a@example.com",
        "password": "strong-password",
        "full_name": "Test User",
    }
    assert client.post("/api/v1/auth/signup", json=payload).status_code == 201
    response = client.post("/api/v1/auth/signup", json={**payload, "email": "A@example.com"})
    assert response.status_code == 409


def post_order(client, headers, key="shared", quantity=1, currency="USD"):
    return client.post(
        "/api/v1/orders",
        headers={**headers, "Idempotency-Key": key},
        json={"items": [{"sku": "SKU", "quantity": quantity}], "currency": currency},
    )


def test_customer_pagination_counts_all_matching_orders(client, db, monkeypatch):
    headers = auth_header(client, "a@example.com", "strong-password")
    other = auth_header(client, "b@example.com", "strong-password")
    for i in range(3):
        assert post_order(client, headers, key=str(i)).status_code == 202
    post_order(client, other)
    response = client.get("/api/v1/orders?limit=2&offset=1", headers=headers)
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2
    assert response.json()["total"] == 3


def test_idempotency_is_customer_scoped(client, db, monkeypatch):
    a = auth_header(client, "a@example.com", "strong-password")
    b = auth_header(client, "b@example.com", "strong-password")
    first, second = post_order(client, a), post_order(client, b)
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] != second.json()["id"]
    assert db.query(Order).count() == 2


def test_idempotency_payload_conflict_and_replay(client, db, monkeypatch):
    headers = auth_header(client, "a@example.com", "strong-password")
    first = post_order(client, headers)
    replay = post_order(client, headers, currency="usd")
    assert first.json()["id"] == replay.json()["id"]
    assert db.query(OutboxEvent).count() == db.query(Order).count() == 1
    conflict = post_order(client, headers, quantity=2)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_key_conflict"


def test_idempotency_insert_race(client, db, monkeypatch):
    headers = auth_header(client, "a@example.com", "strong-password")
    first = post_order(client, headers)
    original = OrderRepository.get_by_idempotency_key
    calls = 0

    def miss_once(self, *args):
        nonlocal calls
        calls += 1
        return None if calls == 1 else original(self, *args)

    monkeypatch.setattr(OrderRepository, "get_by_idempotency_key", miss_once)
    replay = post_order(client, headers)
    assert replay.status_code == 202
    assert replay.json()["id"] == first.json()["id"]
    assert db.query(OutboxEvent).count() == 1


def test_signup_ignores_privileged_role(client):
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "tenant_slug": "default",
            "email": "escalate@example.com",
            "password": "strong-password",
            "full_name": "Test User",
            "role": "admin",
        },
    )
    assert response.status_code == 201
    assert response.json()["role"] == "customer"
    headers = auth_header(client, "escalate@example.com", "strong-password")
    assert client.get("/api/v1/inventory", headers=headers).status_code == 403
