from uuid import uuid4

import pytest

from app.auth.principal import Principal
from app.auth.security import create_access_token
from app.models.entities import Tenant, User
from app.models.enums import UserRole
from app.schemas.tenancy import ApiKeyCreate
from app.services.tenancy import create_api_key


@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.VENDOR, UserRole.CUSTOMER])
def test_dashboard_tenant_projection_is_scoped_and_has_no_secrets(client, db, role):
    for slug in ("dashboard-a", "dashboard-b"):
        tenant = Tenant(id=uuid4(), name=slug, slug=slug, default_currency="USD")
        db.add(tenant)
        db.flush()
        user = User(
            tenant_id=tenant.id,
            email=f"{slug}@example.test",
            full_name="Staff",
            role=role,
            hashed_password="PRIVATE_HASH",
        )
        db.add(user)
        db.commit()
        auth = {"Authorization": "Bearer " + create_access_token(str(user.id))}
        result = client.get("/api/v1/tenants/me", headers=auth)
        if role == UserRole.CUSTOMER:
            assert result.status_code == 403
        else:
            assert result.status_code == 200
            assert result.json() == {
                "id": str(tenant.id),
                "name": slug,
                "slug": slug,
                "status": "active",
                "default_currency": "USD",
            }
        assert "PRIVATE_HASH" not in result.text and "owner_password" not in result.text


def test_dashboard_tenant_requires_staff_jwt_and_active_tenant(client, db):
    assert client.get("/api/v1/tenants/me").status_code == 401
    tenant = Tenant(id=uuid4(), name="Staff store", slug="staff-store")
    db.add(tenant)
    db.flush()
    owner = User(
        tenant_id=tenant.id,
        email="staff@example.test",
        full_name="Staff",
        role=UserRole.ADMIN,
        hashed_password="unused",
    )
    root = User(
        tenant_id=None,
        email="root@example.test",
        full_name="Root",
        role=UserRole.ADMIN,
        hashed_password="unused",
        is_platform_admin=True,
    )
    db.add_all([owner, root])
    db.commit()
    _, key = create_api_key(db, Principal(tenant.id, user=owner), ApiKeyCreate(name="read", scopes=["orders:read"]))
    assert client.get("/api/v1/tenants/me", headers={"X-API-Key": key}).status_code == 403
    assert (
        client.get(
            "/api/v1/tenants/me", headers={"Authorization": "Bearer " + create_access_token(str(root.id))}
        ).status_code
        == 403
    )
    tenant.status = "suspended"
    db.commit()
    response = client.get(
        "/api/v1/tenants/me", headers={"Authorization": "Bearer " + create_access_token(str(owner.id))}
    )
    assert response.status_code == 403 and response.json()["code"] == "tenant_suspended"
