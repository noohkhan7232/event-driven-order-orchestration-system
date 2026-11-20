from datetime import UTC, datetime, timedelta
from unittest.mock import patch


def register_owner(client, slug="store-alpha", email="owner-alpha@example.com"):
    response = client.post(
        "/api/v1/tenants/register",
        json={
            "business_name": "Alpha Store",
            "slug": slug,
            "owner_email": email,
            "owner_password": "strong-password",
            "owner_name": "Store Owner",
        },
    )
    assert response.status_code == 201, response.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "strong-password"})
    assert login.status_code == 200
    return response.json(), {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_tenant_register_atomic_conflicts_and_signup(client, db):
    from app.models.entities import Tenant, User

    tenant, headers = register_owner(client)
    assert db.query(User).filter_by(email="owner-alpha@example.com").one().tenant_id is not None
    payload = {
        "business_name": "Other",
        "slug": "store-alpha",
        "owner_email": "new@example.com",
        "owner_password": "strong-password",
        "owner_name": "Other Owner",
    }
    assert client.post("/api/v1/tenants/register", json=payload).status_code == 409
    payload.update(slug="store-other", owner_email="owner-alpha@example.com")
    assert client.post("/api/v1/tenants/register", json=payload).status_code == 409
    assert db.query(Tenant).filter_by(slug="store-other").count() == 0
    customer = {
        "email": "customer-new@example.com",
        "password": "strong-password",
        "full_name": "Customer",
        "role": "admin",
    }
    assert client.post("/api/v1/auth/signup", json=customer).status_code == 422
    assert client.post("/api/v1/auth/signup", json={**customer, "tenant_slug": "absent"}).status_code == 404
    response = client.post("/api/v1/auth/signup", json={**customer, "tenant_slug": "store-alpha"})
    assert response.status_code == 201 and response.json()["role"] == "customer"


def test_api_key_show_once_hash_uniform_auth_and_throttle(client, db, capsys):
    from app.models.entities import ApiKey

    tenant, owner = register_owner(client)
    response = client.post(
        "/api/v1/api-keys", headers=owner, json={"name": "Read integration", "scopes": ["orders:read"]}
    )
    assert response.status_code == 201, response.text
    created = response.json()
    key = created["key"]
    row = db.query(ApiKey).one()
    assert row.key_prefix == key[:12] and key not in str(row.__dict__)
    assert key not in client.get("/api/v1/api-keys", headers=owner).text
    with patch("app.auth.principal.hmac.compare_digest", wraps=__import__("hmac").compare_digest) as compare:
        assert client.get("/api/v1/orders", headers={"X-API-Key": key}).status_code == 200
        assert compare.called
    used = row.last_used_at
    assert used is not None
    assert client.get("/api/v1/orders", headers={"X-API-Key": key}).status_code == 200
    db.refresh(row)
    assert row.last_used_at == used
    assert (
        client.post(
            "/api/v1/orders",
            headers={"X-API-Key": key, "Idempotency-Key": "denied"},
            json={"items": [{"sku": "SKU", "quantity": 1}]},
        ).status_code
        == 403
    )
    assert client.get("/api/v1/api-keys", headers={"X-API-Key": key}).status_code == 403
    assert client.get("/api/v1/orders", headers={**owner, "X-API-Key": key}).status_code == 400
    failures = []
    for invalid in ["bad", key[:-1] + ("x" if key[-1] != "x" else "y"), "oos_live_abcdefgh_" + "z" * 40]:
        with patch("app.auth.principal.hmac.compare_digest", wraps=__import__("hmac").compare_digest) as compared:
            result = client.get("/api/v1/orders", headers={"X-API-Key": invalid})
            assert compared.called
        assert result.status_code == 401
        failures.append(result.json())
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    failures.append(client.get("/api/v1/orders", headers={"X-API-Key": key}).json())
    row.expires_at = None
    db.commit()
    assert client.delete(f"/api/v1/api-keys/{row.id}", headers=owner).status_code == 204
    assert client.delete(f"/api/v1/api-keys/{row.id}", headers=owner).status_code == 204
    failures.append(client.get("/api/v1/orders", headers={"X-API-Key": key}).json())
    assert all(body == failures[0] for body in failures)
    assert key not in capsys.readouterr().out


def test_suspended_tenant_denies_jwt_and_key(client, db):
    from app.models.entities import Tenant

    tenant, owner = register_owner(client)
    created = client.post("/api/v1/api-keys", headers=owner, json={"name": "integration", "scopes": ["orders:read"]})
    assert created.status_code == 201
    row = db.query(Tenant).filter_by(slug="store-alpha").one()
    row.status = "suspended"
    db.commit()
    for headers in [owner, {"X-API-Key": created.json()["key"]}]:
        response = client.get("/api/v1/orders", headers=headers)
        assert response.status_code == 403 and response.json()["code"] == "tenant_suspended"


def test_unknown_signup_tenant_is_generic_even_for_known_email(client):
    register_owner(client)
    result = client.post(
        "/api/v1/auth/signup",
        json={
            "tenant_slug": "missing-store",
            "email": "owner-alpha@example.com",
            "password": "strong-password",
            "full_name": "Customer",
        },
    )
    assert result.status_code == 404 and result.json()["code"] == "not_found"
