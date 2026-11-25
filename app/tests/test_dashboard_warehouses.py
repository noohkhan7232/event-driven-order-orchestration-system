import pytest

from app.auth.principal import Principal
from app.auth.security import create_access_token
from app.models.entities import Tenant, User, Warehouse
from app.models.enums import UserRole
from app.schemas.tenancy import ApiKeyCreate
from app.services.tenancy import create_api_key


@pytest.mark.parametrize("role", ["admin", "vendor", "customer", "key"])
def test_dashboard_warehouse_list_is_scoped_paginated_and_safe(client, db, role):
    a, b = Tenant(name="First", slug="warehouse-first"), Tenant(name="Second", slug="warehouse-second")
    db.add_all([a, b])
    db.flush()
    user = User(
        tenant_id=b.id,
        email="warehouse@example.test",
        full_name="Staff",
        hashed_password="PRIVATE_HASH",
        role=UserRole.ADMIN if role == "key" else UserRole(role),
    )
    db.add(user)
    db.add_all(
        [
            Warehouse(tenant_id=a.id, code="FOREIGN", name="Foreign", region="IN"),
            Warehouse(tenant_id=b.id, code="LOCAL-1", name="Local one", region="IN"),
            Warehouse(tenant_id=b.id, code="LOCAL-2", name="Local two", region="IN"),
        ]
    )
    db.commit()
    path = "/api/v1/inventory/warehouses"
    assert client.get(path).status_code == 401
    auth = {"Authorization": "Bearer " + create_access_token(str(user.id))}
    if role == "key":
        _, key = create_api_key(
            db, Principal(b.id, user=user), ApiKeyCreate(name="inventory", scopes=["inventory:read"])
        )
        auth = {"X-API-Key": key}
    result = client.get(path + "?limit=1&offset=1", headers=auth)
    assert result.status_code == (403 if role == "customer" else 200)
    if role != "customer":
        data = result.json()
        assert data["total"] == 2 and data["offset"] == 1 and data["limit"] == 1
        assert len(data["items"]) == 1 and data["items"][0]["code"] == "LOCAL-2"
        assert set(data["items"][0]) == {"id", "code", "name", "region"}
        assert "FOREIGN" not in result.text and "PRIVATE_HASH" not in result.text
        assert client.get(path + "?limit=101", headers=auth).status_code == 422
