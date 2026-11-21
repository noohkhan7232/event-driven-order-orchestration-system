from uuid import uuid4

from test_principal_auth import register_owner

from app.auth.security import hash_password
from app.models.entities import AuditLog, User
from app.models.enums import UserRole
from app.services.auth import AuthService


def test_platform_admin_suspends_activates_and_audits(client, db):
    tenant, owner = register_owner(client)
    root = User(
        email="platform@example.com",
        full_name="Platform",
        hashed_password=hash_password("strong-password"),
        role=UserRole.ADMIN,
        is_platform_admin=True,
    )
    db.add(root)
    db.commit()
    headers = {"Authorization": f"Bearer {AuthService(db).login(root.email, 'strong-password')}"}
    base = "/api/v1/admin/tenants"
    assert client.get(base, headers=owner).status_code == 403
    assert client.get(base).status_code == 401
    key = client.post(
        "/api/v1/api-keys", headers=owner, json={"name": "integration", "scopes": ["orders:read"]}
    ).json()["key"]
    assert client.get(base, headers={"X-API-Key": key}).status_code == 403
    listing = client.get(base + "?limit=1", headers=headers)
    assert listing.status_code == 200 and listing.json()["total"] == 2 and len(listing.json()["items"]) == 1
    assert client.get(f"{base}/{uuid4()}", headers=headers).status_code == 404
    assert client.get(f"{base}/{tenant['id']}", headers=headers).status_code == 200
    assert client.post(f"{base}/{tenant['id']}/suspend", headers=owner).status_code == 403
    assert client.post(f"{base}/{tenant['id']}/suspend", headers=headers).json()["status"] == "suspended"
    for credentials in [owner, {"X-API-Key": key}]:
        response = client.get("/api/v1/orders", headers=credentials)
        assert response.status_code == 403 and response.json()["code"] == "tenant_suspended"
    assert client.get(base + "?status=suspended", headers=headers).json()["total"] == 1
    assert client.post(f"{base}/{tenant['id']}/activate", headers=headers).json()["status"] == "active"
    for credentials in [owner, {"X-API-Key": key}]:
        assert client.get("/api/v1/orders", headers=credentials).status_code == 200
    audits = db.query(AuditLog).filter(AuditLog.action.in_(["tenant_suspended", "tenant_activated"])).all()
    assert len(audits) == 2 and all(row.actor_id == root.id for row in audits)


def test_inflight_workflow_continues_for_suspended_tenant(db, workflow):
    from test_consumers import consume

    from app.events.contracts import EventNames
    from app.models.entities import Tenant
    from app.models.enums import OrderStatus

    order, stock = workflow
    tenant = db.get(Tenant, order.tenant_id)
    tenant.status = "suspended"
    db.commit()
    for kind in [EventNames.ORDER_CREATED, EventNames.INVENTORY_RESERVED, EventNames.PAYMENT_AUTHORIZED]:
        consume(order, kind)
    assert order.status == OrderStatus.FULFILLMENT_REQUESTED
    assert stock.available_quantity == 9 and stock.reserved_quantity == 1
