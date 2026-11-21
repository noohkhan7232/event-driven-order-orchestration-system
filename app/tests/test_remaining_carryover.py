import hashlib
from contextlib import nullcontext
from uuid import UUID

from test_principal_auth import register_owner

from app.models.entities import ApiKey, AuditLog, Inventory, Tenant, Warehouse
from app.workers import tasks


def test_adjustment_audit_validation_and_patch_lock(client, db, monkeypatch):
    _, headers = register_owner(client)
    warehouse = client.post(
        "/api/v1/inventory/warehouses", headers=headers, json={"code": "TEST", "name": "Warehouse", "region": "test"}
    ).json()
    client.post("/api/v1/products", headers=headers, json={"sku": "STOCK", "name": "Stock", "price": "1"})
    stock = client.post(
        "/api/v1/inventory",
        headers=headers,
        json={"sku": "STOCK", "warehouse_id": warehouse["id"], "available_quantity": 10},
    ).json()
    path = f"/api/v1/inventory/{stock['id']}"
    result = client.post(path + "/adjustments", headers=headers, json={"delta": -3, "reason": "Count correction"})
    assert result.status_code == 200 and result.json()["available_quantity"] == 7
    audit = db.query(AuditLog).filter_by(action="inventory_adjusted").one()
    assert audit.actor_id and audit.entity_id == UUID(stock["id"])
    assert audit.payload == {"old": 10, "new": 7, "delta": -3, "reason": "Count correction"}
    for body in [{"delta": -8, "reason": "Too low"}, {"delta": 0, "reason": "Zero"}, {"delta": 1, "reason": "x" * 201}]:
        rejected = client.post(path + "/adjustments", headers=headers, json=body)
        assert rejected.status_code == 422
        if body["delta"] == -8:
            assert rejected.json()["code"] == "negative_stock"
    statements = []
    original = db.scalar

    def capture(statement, *args, **kwargs):
        from sqlalchemy.dialects.postgresql import dialect

        statements.append(str(statement.compile(dialect=dialect())))
        return original(statement, *args, **kwargs)

    monkeypatch.setattr(db, "scalar", capture)
    assert client.patch(path, headers=headers, json={"available_quantity": 9}).status_code == 200
    assert any("FROM inventory" in sql and "FOR UPDATE" in sql for sql in statements)
    assert db.get(Inventory, UUID(stock["id"])).available_quantity == 9
    integration = client.post(
        "/api/v1/api-keys", headers=headers, json={"name": "Stock integration", "scopes": ["inventory:write"]}
    ).json()
    assert (
        client.post(
            path + "/adjustments",
            headers={"X-API-Key": integration["key"]},
            json={"delta": 1, "reason": "Key correction"},
        ).status_code
        == 200
    )
    key_audit = db.query(AuditLog).filter_by(action="inventory_adjusted", actor_id=None).one()
    assert key_audit.payload["api_key_id"] == integration["id"]


def test_unscoped_repository_methods_cannot_return():
    from app.repositories.base import Repository
    from app.repositories.inventory import InventoryRepository
    from app.services.inventory import InventoryService

    assert not any(hasattr(Repository, name) for name in ["get", "list"])
    assert not hasattr(InventoryRepository, "get_by_sku")
    assert not any(hasattr(InventoryService, name) for name in ["get_or_404", "low_stock_items"])


def test_low_stock_scan_crosses_pages_and_keeps_tenants_separate(db, monkeypatch):
    tenants = [Tenant(name=f"Store {i}", slug=f"scan-store-{i}") for i in range(2)]
    db.add_all(tenants)
    db.flush()
    for tenant in tenants:
        warehouse = Warehouse(tenant_id=tenant.id, code="SHARED", name="Warehouse", region="test")
        db.add(warehouse)
        db.flush()
        db.add_all(
            [
                Inventory(
                    tenant_id=tenant.id,
                    sku=f"LOW-{i}",
                    product_name="Low",
                    warehouse_id=warehouse.id,
                    available_quantity=0,
                )
                for i in range(105)
            ]
        )
    db.commit()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: nullcontext(db))
    counts = tasks.check_low_stock.run()
    assert all(counts[str(tenant.id)] == 105 for tenant in tenants)
    assert sum(counts.values()) == 210


def test_new_and_legacy_key_prefixes_authenticate(client, db):
    _, headers = register_owner(client)
    created = client.post(
        "/api/v1/api-keys", headers=headers, json={"name": "Modern", "scopes": ["orders:read"]}
    ).json()
    key = created["key"]
    row = db.get(ApiKey, UUID(created["id"]))
    assert row.key_prefix == key[:17] and row.key_hash == hashlib.sha256(key[17:].encode()).hexdigest()
    assert client.get("/api/v1/orders", headers={"X-API-Key": key}).status_code == 200
    # A real legacy row uses its original 12-character split. The same public
    # nonce may exist in both generations; authentication must try both digests.
    legacy_key = key[:17] + "_" + "z" * 40
    db.add(
        ApiKey(
            tenant_id=row.tenant_id,
            name="Legacy",
            key_prefix=legacy_key[:12],
            key_hash=hashlib.sha256(legacy_key[12:].encode()).hexdigest(),
            scopes=["orders:read"],
        )
    )
    db.commit()
    assert client.get("/api/v1/orders", headers={"X-API-Key": legacy_key}).status_code == 200
    assert client.get("/api/v1/orders", headers={"X-API-Key": key}).status_code == 200


def test_key_prefix_collision_retries_ten_times_without_extra_rows(client, db):
    from unittest.mock import patch

    _, headers = register_owner(client)
    with patch("app.services.tenancy.secrets.choice", return_value="A") as choose:
        assert client.post("/api/v1/api-keys", headers=headers, json={"name": "First", "scopes": []}).status_code == 201
        choose.reset_mock()
        exhausted = client.post("/api/v1/api-keys", headers=headers, json={"name": "Collision", "scopes": []})
        assert exhausted.status_code == 409 and choose.call_count == 80
    assert db.query(ApiKey).count() == 1
