import pytest

from app.auth.security import create_access_token
from app.models.entities import Tenant, User
from app.models.enums import UserRole


@pytest.mark.parametrize("role", ["platform", "admin", "vendor", "customer"])
def test_dashboard_tenant_search_requires_platform_and_preserves_safe_filters(client, db, role):
    a = Tenant(name="Needle Store", slug="needle-store")
    b = Tenant(name="Other Store", slug="other-store", status="suspended")
    c = Tenant(name="Literal %_ name", slug="literal-store")
    db.add_all([a, b, c])
    db.flush()
    user = User(
        tenant_id=None if role == "platform" else a.id,
        is_platform_admin=role == "platform",
        role=UserRole.ADMIN if role == "platform" else UserRole(role),
        email="search@example.test",
        full_name="Staff",
        hashed_password="PRIVATE_HASH",
    )
    db.add(user)
    db.commit()
    path = "/api/v1/admin/tenants"
    assert client.get(path + "?q=Needle").status_code == 401
    auth = {"Authorization": "Bearer " + create_access_token(str(user.id))}
    result = client.get(path + "?q=needle&status=active&limit=1&offset=0", headers=auth)
    assert result.status_code == (200 if role == "platform" else 403)
    if role == "platform":
        data = result.json()
        assert data["total"] == 1 and data["items"][0]["id"] == str(a.id)
        assert set(data["items"][0]) == {"id", "name", "slug", "status", "default_currency"}
        assert "PRIVATE_HASH" not in result.text
        assert client.get(path + "?q=other-store&status=active", headers=auth).json()["total"] == 0
        literal = client.get(path + "?q=%25_", headers=auth).json()
        assert literal["total"] == 1 and literal["items"][0]["id"] == str(c.id)
        assert client.get(path + "?q=" + "x" * 161, headers=auth).status_code == 422
