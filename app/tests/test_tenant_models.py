import pytest


@pytest.mark.parametrize(
    "slug", ["admin", "api", "www", "app", "system", "health", "metrics", "ab", "Bad-Store", "bad space", "-bad"]
)
def test_tenant_slug_rejects_reserved_or_invalid_values(slug):
    from app.models.entities import Tenant

    with pytest.raises(ValueError, match="slug"):
        Tenant(name="Store", slug=slug)


def test_user_tenant_principal_invariant():
    from uuid import uuid4

    from app.models.entities import User

    with pytest.raises(ValueError):
        User(is_platform_admin=False, tenant_id=None).validate_tenancy()
    with pytest.raises(ValueError):
        User(is_platform_admin=True, tenant_id=uuid4()).validate_tenancy()
    User(is_platform_admin=True, tenant_id=None).validate_tenancy()
    User(is_platform_admin=False, tenant_id=uuid4()).validate_tenancy()
