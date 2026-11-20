from contextlib import nullcontext
from uuid import UUID

from test_principal_auth import register_owner

from app.models.entities import OutboxEvent
from app.workers import tasks


def product(client, headers, sku="PRICED", price="19.99", currency="USD"):
    response = client.post(
        "/api/v1/products",
        headers=headers,
        json={"sku": sku, "name": "Original Name", "price": price, "currency": currency},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_catalog_price_snapshot_replay_and_pii_exclusion(client, db, monkeypatch, capsys):
    tenant, headers = register_owner(client)
    item = product(client, headers)
    buyer = {"buyer_name": "Private Buyer", "buyer_email": "private-buyer@example.com", "buyer_phone": "+919999999999"}
    body = {"items": [{"sku": "PRICED", "quantity": 3}], "external_reference": "reference-1", **buyer}
    request_headers = {**headers, "Idempotency-Key": "snapshot"}
    response = client.post("/api/v1/orders", headers=request_headers, json=body)
    assert response.status_code == 202, response.text
    order = response.json()
    assert order["total_amount"] == "59.97" and order["items"][0]["unit_price"] == "19.99"
    assert all(order[k] == v for k, v in buyer.items())
    assert (
        client.patch(
            f"/api/v1/products/{item['id']}", headers=headers, json={"price": "25.00", "name": "Changed Name"}
        ).status_code
        == 200
    )
    warehouse = client.post(
        "/api/v1/inventory/warehouses", headers=headers, json={"code": "TEST", "name": "Warehouse", "region": "test"}
    )
    assert warehouse.status_code == 201
    stock = client.post(
        "/api/v1/inventory",
        headers=headers,
        json={
            "sku": "PRICED",
            "product_name": "Client lie",
            "warehouse_id": warehouse.json()["id"],
            "available_quantity": 10,
        },
    )
    assert stock.status_code == 201 and stock.json()["product_name"] == "Changed Name"
    monkeypatch.setattr(tasks, "SessionLocal", lambda: nullcontext(db))
    event = db.query(OutboxEvent).filter_by(aggregate_id=UUID(order["id"]), event_type="order.created").one()
    tasks.process_event.run(event.payload)
    replay = client.post("/api/v1/orders", headers=request_headers, json=body)
    assert replay.status_code == 202 and replay.json()["id"] == order["id"]
    assert replay.json()["total_amount"] == "59.97" and replay.json()["items"][0]["product_name"] == "Original Name"
    assert db.query(OutboxEvent).filter_by(event_type="order.created").count() == 1
    serialized = " ".join(str(row.payload) for row in db.query(OutboxEvent))
    output = capsys.readouterr().out
    assert all(value not in serialized and value not in output for value in buyer.values())


def test_catalog_validation_and_currency_errors(client, db):
    tenant, headers = register_owner(client)
    item = product(client, headers)
    zero = product(client, headers, sku="FREE", price="0")
    product(client, headers, sku="EURO", currency="EUR")

    def post(items, currency=None):
        return client.post(
            "/api/v1/orders",
            headers={**headers, "Idempotency-Key": "-".join(i["sku"] for i in items) + str(currency)},
            json={"items": items, **({"currency": currency} if currency else {})},
        )

    result = post([{"sku": "MISSING", "quantity": 1}])
    assert result.status_code == 422 and result.json()["code"] == "unknown_or_inactive_sku"
    mixed = post([{"sku": "PRICED", "quantity": 1}, {"sku": "EURO", "quantity": 1}])
    assert mixed.status_code == 422 and mixed.json()["code"] == "mixed_currency"
    assert post([{"sku": "PRICED", "quantity": 1}], "INR").status_code == 422
    assert post([{"sku": "FREE", "quantity": 1}]).json()["total_amount"] == "0.00"
    assert client.patch(f"/api/v1/products/{item['id']}", headers=headers, json={"is_active": False}).status_code == 200
    inactive = post([{"sku": "PRICED", "quantity": 2}])
    assert inactive.status_code == 422
    assert (
        client.post(
            "/api/v1/products",
            headers=headers,
            json={"sku": "PRICED", "name": "Duplicate", "price": "1", "currency": "USD"},
        ).status_code
        == 409
    )
    assert client.patch(f"/api/v1/products/{zero['id']}", headers=headers, json={"sku": "MUTATED"}).status_code == 422
    for bad in ["-1.00", "1.001"]:
        assert (
            client.post(
                "/api/v1/products", headers=headers, json={"sku": "BAD", "name": "Bad", "price": bad, "currency": "USD"}
            ).status_code
            == 422
        )
    warehouse = client.post(
        "/api/v1/inventory/warehouses", headers=headers, json={"code": "VALID", "name": "Warehouse", "region": "test"}
    ).json()
    assert (
        client.post(
            "/api/v1/inventory",
            headers=headers,
            json={"sku": "UNKNOWN", "warehouse_id": warehouse["id"], "available_quantity": 1},
        ).status_code
        == 422
    )


def test_catalog_pagination_filters_and_order_visibility(client, db):
    from conftest import TestingSessionLocal

    from app.auth.security import hash_password
    from app.models.entities import User
    from app.models.enums import UserRole

    tenant, owner = register_owner(client)
    product(client, owner)
    product(client, owner, sku="OTHER")
    assert client.get("/api/v1/products?limit=1&q=PRI", headers=owner).json()["total"] == 1
    for index in range(3):
        response = client.post(
            "/api/v1/orders",
            headers={**owner, "Idempotency-Key": str(index)},
            json={
                "items": [{"sku": "PRICED", "quantity": 1}],
                "external_reference": "needle" if index < 2 else "other",
            },
        )
        assert response.status_code == 202
    listing = client.get("/api/v1/orders?external_reference=needle&status=created&limit=1", headers=owner)
    assert listing.status_code == 200 and listing.json()["total"] == 2 and len(listing.json()["items"]) == 1
    for query in ["created_from=2100-01-01T00:00:00Z", "created_to=2000-01-01T00:00:00Z"]:
        filtered = client.get(f"/api/v1/orders?{query}", headers=owner)
        assert filtered.status_code == 200 and filtered.json()["total"] == 0 and filtered.json()["items"] == []
    with TestingSessionLocal.begin() as setup:
        setup.add(
            User(
                tenant_id=UUID(tenant["id"]),
                email="other-customer@example.com",
                full_name="Customer",
                hashed_password=hash_password("strong-password"),
                role=UserRole.CUSTOMER,
            )
        )
    login = client.post(
        "/api/v1/auth/login", json={"email": "other-customer@example.com", "password": "strong-password"}
    )
    customer = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get("/api/v1/orders", headers=customer).json()["total"] == 0
    assert client.get("/api/v1/products", headers=customer).status_code == 403


def test_order_total_overflow_is_rejected_before_persistence(client):
    _, headers = register_owner(client)
    product(client, headers, price="9999999999.99")
    response = client.post(
        "/api/v1/orders",
        headers={**headers, "Idempotency-Key": "overflow"},
        json={"items": [{"sku": "PRICED", "quantity": 500}]},
    )
    assert response.status_code == 422 and response.json()["code"] == "order_total_out_of_range"


def test_database_exception_log_does_not_include_buyer_values(client, db, monkeypatch, capsys):
    from fastapi.testclient import TestClient

    from app.main import app

    _, headers = register_owner(client)
    product(client, headers)
    dialect = db.get_bind().dialect
    original = dialect.do_execute

    def unavailable(cursor, statement, parameters, context=None):
        if statement.startswith("INSERT INTO orders"):
            raise dialect.dbapi.OperationalError("injected database unavailable")
        return original(cursor, statement, parameters, context)

    monkeypatch.setattr(dialect, "do_execute", unavailable)
    with TestClient(app, raise_server_exceptions=False) as safe:
        response = safe.post(
            "/api/v1/orders",
            headers={**headers, "Idempotency-Key": "private-error"},
            json={
                "items": [{"sku": "PRICED", "quantity": 1}],
                "buyer_name": "Synthetic Secret Buyer",
                "buyer_email": "synthetic-private@example.com",
            },
        )
    assert response.status_code == 500
    output = capsys.readouterr().out
    assert "Synthetic Secret Buyer" not in output and "synthetic-private@example.com" not in output


def test_order_catalog_single_query_and_legacy_hash_replay(client, db):
    import hashlib
    import json

    from sqlalchemy import event

    from app.models.entities import Order

    _, headers = register_owner(client)
    item = product(client, headers)
    product(client, headers, sku="SECOND")
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "FROM products" in statement:
            statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        response = client.post(
            "/api/v1/orders",
            headers={**headers, "Idempotency-Key": "legacy"},
            json={"currency": "USD", "items": [{"sku": "PRICED", "quantity": 1}, {"sku": "SECOND", "quantity": 2}]},
        )
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)
    assert response.status_code == 202 and len(statements) == 1
    row = db.get(Order, UUID(response.json()["id"]))
    legacy_body = {"currency": "USD", "items": [{"sku": "PRICED", "quantity": 1}, {"sku": "SECOND", "quantity": 2}]}
    old_hash = hashlib.sha256(json.dumps(legacy_body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    client.patch(f"/api/v1/products/{item['id']}", headers=headers, json={"is_active": False, "price": "25.00"})
    for fingerprint in [old_hash, None]:
        row.request_hash = fingerprint
        db.commit()
        replay = client.post("/api/v1/orders", headers={**headers, "Idempotency-Key": "legacy"}, json=legacy_body)
        assert replay.status_code == 202 and replay.json()["id"] == str(row.id)
        assert replay.json()["total_amount"] == "59.97"
