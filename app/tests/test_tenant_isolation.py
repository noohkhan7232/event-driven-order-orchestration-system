from uuid import UUID

import pytest
from conftest import DEFAULT_TENANT_ID
from sqlalchemy import select

from app.auth.principal import SCOPES, Principal
from app.auth.security import hash_password
from app.models.entities import Inventory, Order, Shipment, Tenant, User, Warehouse
from app.models.enums import UserRole
from app.schemas.orders import OrderCreate, OrderItemCreate
from app.schemas.tenancy import ApiKeyCreate
from app.services.auth import AuthService
from app.services.orders import OrderService
from app.services.tenancy import create_api_key


@pytest.mark.parametrize("role", ["admin", "vendor", "customer", "key"])
def test_tenant_resource_isolation_matrix(client, db, role):
    a, b = Tenant(name="Alpha", slug="alpha-store"), Tenant(name="Beta", slug="beta-store")
    db.add_all([a, b])
    db.flush()
    owner = User(
        tenant_id=a.id,
        email="alpha-isolation@example.com",
        full_name="Alpha",
        hashed_password=hash_password("strong-password"),
        role=UserRole.ADMIN,
    )
    other = User(
        tenant_id=b.id,
        email="beta-isolation@example.com",
        full_name="Beta",
        hashed_password=hash_password("strong-password"),
        role=UserRole.CUSTOMER if role == "key" else UserRole(role),
    )
    db.add_all([owner, other])
    db.flush()
    wa = Warehouse(tenant_id=a.id, code="SAME", name="Alpha", region="test")
    wb = Warehouse(tenant_id=b.id, code="SAME", name="Beta", region="test")
    db.add_all([wa, wb])
    db.flush()
    stocka = Inventory(tenant_id=a.id, sku="SAME", product_name="Alpha", warehouse_id=wa.id, available_quantity=10)
    stockb = Inventory(tenant_id=b.id, sku="SAME", product_name="Beta", warehouse_id=wb.id, available_quantity=20)
    from app.models.entities import Product

    db.add_all(
        [Product(tenant_id=store.id, sku="SAME", name="Snapshot", price=19.99, currency="USD") for store in [a, b]]
    )
    db.add_all([stocka, stockb])
    db.commit()
    order = OrderService(db).create_order(
        owner, OrderCreate(items=[OrderItemCreate(sku="SAME", quantity=1)]), "shared-key"
    )
    shipment = Shipment(order_id=order.id)
    db.add(shipment)
    db.commit()
    keya, _ = create_api_key(db, Principal(a.id, user=owner), ApiKeyCreate(name="alpha", scopes=["orders:read"]))
    product_a = db.scalar(select(Product).where(Product.tenant_id == a.id))
    if role == "key":
        # Owner management requires ADMIN; issue the integration key through a temporary owner role.
        other.role = UserRole.ADMIN
        db.commit()
        _, key = create_api_key(db, Principal(b.id, user=other), ApiKeyCreate(name="beta", scopes=list(SCOPES)))
        headers = {"X-API-Key": key}
    else:
        headers = {"Authorization": f"Bearer {AuthService(db).login(other.email, 'strong-password')}"}
    assert client.get(f"/api/v1/orders/{order.id}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/products/{product_a.id}", headers=headers).status_code == 404
    assert client.patch(f"/api/v1/products/{product_a.id}", headers=headers, json={"price": "0"}).status_code == 404
    catalog = client.get("/api/v1/products", headers=headers)
    assert catalog.status_code == (403 if role == "customer" else 200)
    if role != "customer":
        assert catalog.json()["total"] == 1
        assert all(row["id"] != str(product_a.id) for row in catalog.json()["items"])
    new_product = client.post(
        "/api/v1/products", headers=headers, json={"sku": "B-ONLY", "name": "Beta only", "price": "1.00"}
    )
    assert new_product.status_code == (403 if role == "customer" else 201)
    warehouse = client.post(
        "/api/v1/inventory/warehouses", headers=headers, json={"code": "B-ONLY", "name": "Beta", "region": "test"}
    )
    assert warehouse.status_code == (403 if role == "customer" else 201)
    key_creation = client.post(
        "/api/v1/api-keys", headers=headers, json={"name": "Beta managed", "scopes": ["orders:read"]}
    )
    assert key_creation.status_code == (201 if role == "admin" else 403)
    assert client.post(f"/api/v1/orders/{order.id}/cancel", headers=headers).status_code == 404
    assert client.get(f"/api/v1/inventory/{stocka.id}", headers=headers).status_code == 404
    assert (
        client.post(
            f"/api/v1/inventory/{stocka.id}/adjustments",
            headers=headers,
            json={"delta": 1, "reason": "Foreign attempt"},
        ).status_code
        == 404
    )
    own_adjustment = client.post(
        f"/api/v1/inventory/{stockb.id}/adjustments",
        headers=headers,
        json={"delta": 1, "reason": "Owned adjustment"},
    )
    assert own_adjustment.status_code == (403 if role == "customer" else 200)
    assert (
        client.post(
            f"/api/v1/shipments/{shipment.id}/events", headers=headers, json={"status": "delivered"}
        ).status_code
        == 404
    )
    assert client.delete(f"/api/v1/api-keys/{keya.id}", headers=headers).status_code == 404
    listed = client.get("/api/v1/orders", headers=headers)
    assert listed.status_code == 200 and listed.json()["total"] == 0 and listed.json()["items"] == []
    inventories = client.get("/api/v1/inventory", headers=headers)
    if role == "customer":
        assert inventories.status_code == 403
    else:
        assert inventories.status_code == 200 and inventories.json()["total"] == 1
        assert inventories.json()["items"][0]["id"] == str(stockb.id)
    assert (
        client.patch(f"/api/v1/inventory/{stocka.id}", headers=headers, json={"available_quantity": 0}).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/inventory",
            headers=headers,
            json={"sku": "SAME", "product_name": "Ignored", "warehouse_id": str(wa.id), "available_quantity": 1},
        ).status_code
        == 404
    )
    low = client.get("/api/v1/inventory/low-stock", headers=headers)
    assert low.status_code == (403 if role == "customer" else 200)
    if low.status_code == 200:
        assert low.json() == []
    keys = client.get("/api/v1/api-keys", headers=headers)
    assert keys.status_code == (200 if role == "admin" else 403)
    if keys.status_code == 200:
        assert str(keya.id) not in keys.text
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == (403 if role == "key" else 200)
    if me.status_code == 200:
        assert me.json()["tenant_id"] == str(b.id)
    created = client.post(
        "/api/v1/orders",
        headers={**headers, "Idempotency-Key": "shared-key"},
        json={"items": [{"sku": "SAME", "quantity": 1}]},
    )
    assert created.status_code == 202
    assert created.json()["id"] != str(order.id)
    assert db.get(Order, UUID(created.json()["id"])).tenant_id == b.id
    db.refresh(shipment)
    assert shipment.status.value == "pending"
    assert db.scalar(select(Inventory.available_quantity).where(Inventory.id == stocka.id)) == 10


def test_route_registry_has_no_unclassified_routes():
    from app.main import app

    public_webhook = {("POST", "/api/v1/webhooks/payments/{provider}/{webhook_public_id}")}
    public = {
        ("GET", "/docs"),
        ("HEAD", "/docs"),
        ("GET", "/docs/oauth2-redirect"),
        ("HEAD", "/docs/oauth2-redirect"),
        ("GET", "/redoc"),
        ("HEAD", "/redoc"),
        ("GET", "/api/v1/openapi.json"),
        ("HEAD", "/api/v1/openapi.json"),
        ("POST", "/api/v1/auth/signup"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/tenants/register"),
        ("GET", "/api/v1/system/health"),
        ("GET", "/api/v1/system/ready"),
    }
    tenant = {
        ("PATCH", "/api/v1/shipments/{shipment_id}"),
        ("GET", "/api/v1/settings/shipping"),
        ("PATCH", "/api/v1/settings/shipping"),
        ("PUT", "/api/v1/settings/payments/razorpay"),
        ("GET", "/api/v1/settings/payments/razorpay"),
        ("DELETE", "/api/v1/settings/payments/razorpay"),
        ("GET", "/api/v1/products"),
        ("POST", "/api/v1/products"),
        ("GET", "/api/v1/products/{product_id}"),
        ("PATCH", "/api/v1/products/{product_id}"),
        ("GET", "/api/v1/auth/me"),
        ("GET", "/api/v1/orders"),
        ("POST", "/api/v1/orders"),
        ("GET", "/api/v1/orders/{order_id}"),
        ("POST", "/api/v1/orders/{order_id}/cancel"),
        ("GET", "/api/v1/inventory"),
        ("POST", "/api/v1/inventory"),
        ("POST", "/api/v1/inventory/warehouses"),
        ("GET", "/api/v1/inventory/low-stock"),
        ("GET", "/api/v1/inventory/{inventory_id}"),
        ("PATCH", "/api/v1/inventory/{inventory_id}"),
        ("POST", "/api/v1/inventory/{inventory_id}/adjustments"),
        ("POST", "/api/v1/shipments/{shipment_id}/events"),
        ("GET", "/api/v1/api-keys"),
        ("POST", "/api/v1/api-keys"),
        ("DELETE", "/api/v1/api-keys/{key_id}"),
    }
    platform = {("GET", "/api/v1/system/metrics"), ("GET", "/api/v1/system/workers")}
    platform |= {
        ("GET", "/api/v1/admin/tenants"),
        ("GET", "/api/v1/admin/tenants/{tenant_id}"),
        ("POST", "/api/v1/admin/tenants/{tenant_id}/suspend"),
        ("POST", "/api/v1/admin/tenants/{tenant_id}/activate"),
    }
    actual = {(method, route.path) for route in app.routes for method in route.methods}
    assert actual == public | public_webhook | tenant | platform
    from app.core.config import get_settings
    from app.core.ratelimit import policies_for

    assert all(policies_for(method, path, get_settings()) for method, path in public | public_webhook)


def test_tenant_owner_cannot_read_platform_endpoints(client, db):
    owner = User(
        tenant_id=DEFAULT_TENANT_ID,
        email="owner-platform@example.com",
        full_name="Owner",
        hashed_password=hash_password("strong-password"),
        role=UserRole.ADMIN,
    )
    db.add(owner)
    db.commit()
    headers = {"Authorization": f"Bearer {AuthService(db).login(owner.email, 'strong-password')}"}
    assert client.get("/api/v1/system/metrics", headers=headers).status_code == 403
    assert client.get("/api/v1/system/workers", headers=headers).status_code == 403


def test_platform_admin_access_and_database_authoritative_tenant(client, db):
    from app.auth.security import create_access_token

    platform = User(
        tenant_id=None,
        is_platform_admin=True,
        email="root@example.com",
        full_name="Root",
        hashed_password=hash_password("strong-password"),
        role=UserRole.ADMIN,
    )
    customer = User(
        tenant_id=DEFAULT_TENANT_ID,
        email="db-authority@example.com",
        full_name="Buyer",
        hashed_password=hash_password("strong-password"),
        role=UserRole.CUSTOMER,
    )
    db.add_all([platform, customer])
    db.commit()
    root_headers = {"Authorization": f"Bearer {AuthService(db).login(platform.email, 'strong-password')}"}
    assert client.get("/api/v1/system/metrics", headers=root_headers).status_code == 200
    assert client.get("/api/v1/system/workers", headers=root_headers).status_code == 200
    forged_tid = create_access_token(str(customer.id), {"tid": str(platform.id), "role": "admin"})
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged_tid}"})
    assert response.status_code == 200 and response.json()["tenant_id"] == str(DEFAULT_TENANT_ID)
    assert response.json()["role"] == "customer"
