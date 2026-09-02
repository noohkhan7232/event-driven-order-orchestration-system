from app.models.entities import Inventory, Warehouse
from conftest import auth_header


def test_signup_login_and_me(client):
    response = client.post(
        "/api/v1/auth/signup",
        json={"email": "customer@example.com", "password": "strong-password", "full_name": "Customer One"},
    )
    assert response.status_code == 201

    login = client.post("/api/v1/auth/login", json={"email": "customer@example.com", "password": "strong-password"})
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "customer@example.com"


def test_create_order_is_idempotent(client, db, monkeypatch):
    published = []
    monkeypatch.setattr("app.events.publisher.celery_app.send_task", lambda *args, **kwargs: published.append((args, kwargs)))

    warehouse = Warehouse(code="DFW-01", name="Dallas Fulfillment", region="south")
    db.add(warehouse)
    db.flush()
    db.add(Inventory(sku="SKU-001", product_name="Premium Headphones", warehouse_id=warehouse.id, available_quantity=10))
    db.commit()

    headers = auth_header(client, "buyer@example.com", "strong-password")
    headers["Idempotency-Key"] = "order-req-001"
    payload = {"items": [{"sku": "SKU-001", "quantity": 2}], "currency": "USD"}

    first = client.post("/api/v1/orders", json=payload, headers=headers)
    second = client.post("/api/v1/orders", json=payload, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert len(published) == 1
