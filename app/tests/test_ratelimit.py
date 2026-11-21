import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import pytest
from starlette.requests import Request
from test_principal_auth import register_owner

from app.core.config import get_settings
from app.core.exceptions import AuthorizationError
from app.main import app


def configure(**values):
    settings = get_settings().model_copy(update=values)
    app.dependency_overrides[get_settings] = lambda: settings
    return settings


def test_configured_invite_cannot_be_empty():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, registration_invite_code="")


def test_memory_window_boundary_and_atomic_limit():
    from app.core.ratelimit import MemoryRateLimiter

    now = [100.0]
    limiter = MemoryRateLimiter(clock=lambda: now[0])
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: limiter.hit("shared", 5, 60), range(30)))
    assert sum(result.allowed for result in outcomes) == 5
    assert all(result.remaining >= 0 and result.reset == 160 for result in outcomes)
    assert limiter.hit("other", 5, 60).remaining == 4
    now[0] = 160
    assert limiter.hit("shared", 5, 60).remaining == 4


def test_cold_backend_creation_does_not_split_memory_quotas(monkeypatch):
    import time
    from threading import Barrier

    from app.core import ratelimit

    original = ratelimit.MemoryRateLimiter
    gate = Barrier(8)

    def slow_constructor():
        time.sleep(0.02)
        return original()

    monkeypatch.setattr(ratelimit, "MemoryRateLimiter", slow_constructor)

    def obtain(_):
        gate.wait(timeout=10)
        return ratelimit.get_limiter(None, True)

    with ThreadPoolExecutor(max_workers=8) as pool:
        backends = list(pool.map(obtain, range(8)))
    assert len({id(backend) for backend in backends}) == 1


@pytest.mark.parametrize(
    "count,forwarded,expected",
    [
        (0, "9.9.9.9", "1.2.3.4"),
        (1, "9.9.9.9, 8.8.8.8", "8.8.8.8"),
        (2, "9.9.9.9, 8.8.8.8", "9.9.9.9"),
        (2, "9.9.9.9", "1.2.3.4"),
        (1, "invalid", "1.2.3.4"),
    ],
)
def test_client_ip_ignores_spoof_or_uses_configured_hop(count, forwarded, expected):
    from app.core.ratelimit import client_ip

    request = Request(
        {"type": "http", "client": ("1.2.3.4", 123), "headers": [(b"x-forwarded-for", forwarded.encode())]}
    )
    assert client_ip(request, count) == expected


def test_login_ip_limit_headers_and_spoofed_forwarding(client):
    configure(rate_limit_login_ip_limit=2, rate_limit_login_email_limit=100)
    with patch("app.services.auth.AuthService.login", side_effect=AuthorizationError("Invalid email or password")):
        responses = [
            client.post(
                "/api/v1/auth/login",
                headers={"X-Forwarded-For": f"10.0.0.{i}"},
                json={"email": f"user{i}@example.com", "password": "invalid-password"},
            )
            for i in range(3)
        ]
    assert [response.status_code for response in responses] == [403, 403, 429]
    response = responses[-1]
    assert response.json() == {"code": "rate_limited", "message": "Too many requests", "details": {}}
    assert int(response.headers["Retry-After"]) > 0
    assert response.headers["X-RateLimit-Limit"] == "2"
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert int(response.headers["X-RateLimit-Reset"]) > 0 and response.headers["x-request-id"]


def test_login_email_ip_limit_is_normalized_and_independent(client):
    configure(rate_limit_login_ip_limit=100, rate_limit_login_email_limit=2)
    with patch("app.services.auth.AuthService.login", side_effect=AuthorizationError("Invalid email or password")):
        results = [
            client.post("/api/v1/auth/login", json={"email": email, "password": "invalid-password"}).status_code
            for email in ["Same@Example.com", "same@example.com", "SAME@example.com", "other@example.com"]
        ]
    assert results == [403, 403, 429, 403]


def test_authenticated_quotas_are_per_user_and_key(client):
    from app.core.ratelimit import get_limiter

    _, owner = register_owner(client)
    keys = [
        client.post("/api/v1/api-keys", headers=owner, json={"name": f"Key {i}", "scopes": ["orders:read"]}).json()[
            "key"
        ]
        for i in range(2)
    ]
    configure(rate_limit_authenticated_limit=2)
    get_limiter.cache_clear()
    for credentials in [owner, *[{"X-API-Key": key} for key in keys]]:
        assert [client.get("/api/v1/orders", headers=credentials).status_code for _ in range(3)] == [200, 200, 429]


@pytest.mark.parametrize("fail_open,expected", [(True, 200), (False, 503)])
def test_backend_outage_is_closed_for_login_and_configurable_for_auth(client, monkeypatch, capsys, fail_open, expected):
    from app.core import ratelimit

    _, owner = register_owner(client)
    configure(rate_limit_fail_open=fail_open)

    class Down:
        def hit(self, *args):
            raise ratelimit.BackendUnavailable()

    monkeypatch.setattr(ratelimit, "get_limiter", lambda *args: Down())
    assert client.get("/api/v1/orders", headers=owner).status_code == expected
    login = client.post("/api/v1/auth/login", json={"email": "private@example.com", "password": "invalid-password"})
    assert login.status_code == 503 and login.json()["code"] == "rate_limit_unavailable"
    output = capsys.readouterr().out
    assert "rate_limit_degraded" in output and "private@example.com" not in output
    samples = [
        sample for metric in ratelimit.DEGRADED.collect() for sample in metric.samples if sample.name.endswith("_total")
    ]
    assert any(sample.value > 0 and sample.labels["policy"] == "login_ip" for sample in samples)


def test_registration_switch_and_constant_time_invite_check(client, db):
    from app.models.entities import Tenant

    payload = {
        "business_name": "Closed store",
        "slug": "closed-store",
        "owner_email": "new@example.com",
        "owner_name": "Owner",
        "owner_password": "strong-password",
    }
    configure(registration_enabled=False)
    closed = client.post("/api/v1/tenants/register", json=payload)
    assert closed.status_code == 403 and closed.json()["code"] == "registration_closed"
    from pydantic import SecretStr

    configure(
        registration_enabled=True, registration_invite_code=SecretStr("synthetic-invite"), rate_limit_register_limit=20
    )
    for invite in [None, "incorrect-invite"]:
        with patch("app.services.tenancy.hmac.compare_digest", wraps=__import__("hmac").compare_digest) as compared:
            denied = client.post(
                "/api/v1/tenants/register", json={**payload, **({"invite_code": invite} if invite else {})}
            )
            assert denied.status_code == 403 and compared.called
            assert denied.json() == closed.json()
    created = client.post("/api/v1/tenants/register", json={**payload, "invite_code": "synthetic-invite"})
    assert created.status_code == 201 and "synthetic-invite" not in created.text
    assert db.query(Tenant).filter_by(slug="closed-store").count() == 1


def test_invalid_invite_input_is_not_echoed_in_validation_error(client, capsys):
    response = client.post(
        "/api/v1/tenants/register",
        json={
            "business_name": "Store",
            "owner_email": "new@example.com",
            "owner_name": "Owner",
            "owner_password": "strong-password",
            "invite_code": ["synthetic-private-invite"],
        },
    )
    assert response.status_code == 422
    assert "synthetic-private-invite" not in response.text
    assert "synthetic-private-invite" not in capsys.readouterr().out


def test_missing_registration_field_does_not_echo_other_credentials(client):
    response = client.post(
        "/api/v1/tenants/register",
        json={
            "business_name": "Store",
            "owner_email": "new@example.com",
            "owner_password": "synthetic-private-password",
            "invite_code": "synthetic-private-code",
        },
    )
    assert response.status_code == 422
    assert "synthetic-private-password" not in response.text and "synthetic-private-code" not in response.text


def test_oversized_login_cannot_bypass_ip_quota(client):
    configure(rate_limit_login_ip_limit=1)
    assert (
        client.post(
            "/api/v1/auth/login", content=b"x" * 17000, headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/api/v1/auth/login", content=b"x" * 17000, headers={"Content-Type": "application/json"}
        ).status_code
        == 429
    )


def test_backend_selection_errors_and_memory_expiration_cleanup():
    from unittest.mock import Mock

    from redis.exceptions import ConnectionError

    from app.core.ratelimit import (
        BackendUnavailable,
        MemoryRateLimiter,
        RedisRateLimiter,
        UnavailableRateLimiter,
        get_limiter,
    )

    assert isinstance(get_limiter(None, False), UnavailableRateLimiter)
    assert isinstance(get_limiter("invalid://backend", True), UnavailableRateLimiter)
    assert isinstance(get_limiter("redis://localhost:6379/15", True), RedisRateLimiter)
    with pytest.raises(BackendUnavailable):
        get_limiter(None, False).hit("test", 1, 1)
    transport = Mock()
    transport.eval.return_value = [1, 60000, 100, 0]
    assert RedisRateLimiter(transport).hit("test", 2, 60).remaining == 1
    transport.eval.return_value = "invalid"
    with pytest.raises(BackendUnavailable):
        RedisRateLimiter(transport).hit("test", 2, 60)
    transport.eval.side_effect = ConnectionError("Untrusted transport message")
    with pytest.raises(BackendUnavailable) as failure:
        RedisRateLimiter(transport).hit("test", 2, 60)
    assert str(failure.value) == ""
    now = [0.0]
    memory = MemoryRateLimiter(clock=lambda: now[0])
    for index in range(1025):
        memory.hit(str(index), 1, 1)
    now[0] = 2
    memory.hit("fresh", 1, 1)
    assert len(memory.windows) == 1


def test_invalid_login_json_still_has_quota_and_no_proxy_client(client):
    from app.core.ratelimit import client_ip

    configure(rate_limit_login_ip_limit=10)
    for body in [b"{", b"[]", b'{"email":[]}']:
        assert (
            client.post("/api/v1/auth/login", content=body, headers={"Content-Type": "application/json"}).status_code
            == 422
        )
    assert client_ip(Request({"type": "http", "headers": []}), 0) == "unknown"


@pytest.mark.redis
def test_real_redis_atomic_window_and_expiry():
    from redis import Redis

    from app.core.ratelimit import RedisRateLimiter

    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Requires TEST_REDIS_URL pointing to test Redis")
    client = Redis.from_url(url, socket_timeout=2)
    namespace = "oos:test-rate:" + uuid4().hex + ":"
    limiter = RedisRateLimiter(client, namespace=namespace)
    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: limiter.hit("shared", 7, 60), range(40)))
        assert sum(result.allowed for result in results) == 7
        assert 0 < client.pttl(namespace + "shared") <= 60000
        client.delete(namespace + "shared")
        assert limiter.hit("shared", 7, 60).remaining == 6
    finally:
        client.delete(namespace + "shared")
        client.close()


def test_registration_and_signup_ip_caps_count_rejected_requests(client):
    from app.core.exceptions import ConflictError

    configure(rate_limit_register_limit=2, rate_limit_signup_limit=2)
    cases = [
        (
            "/api/v1/tenants/register",
            "app.api.v1.endpoints.tenancy.register_tenant",
            {
                "business_name": "Store",
                "owner_name": "Owner",
                "owner_email": "new@example.com",
                "owner_password": "strong-password",
            },
        ),
        (
            "/api/v1/auth/signup",
            "app.api.v1.endpoints.auth.AuthService.register",
            {
                "email": "new@example.com",
                "password": "strong-password",
                "full_name": "Customer",
                "tenant_slug": "default",
            },
        ),
    ]
    for path, target, payload in cases:
        with patch(target, side_effect=ConflictError("Conflict")):
            assert [client.post(path, json=payload).status_code for _ in range(3)] == [409, 409, 429]


@pytest.mark.redis
def test_real_redis_protects_the_http_login_route(client):
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Requires TEST_REDIS_URL pointing to test Redis")
    nonce = uuid4().hex[:24]
    ip = "2001:db8:" + ":".join(nonce[index : index + 4] for index in range(0, 24, 4))
    configure(
        rate_limit_redis_url=url, trusted_proxy_count=1, rate_limit_login_ip_limit=2, rate_limit_login_email_limit=100
    )
    with patch("app.services.auth.AuthService.login", side_effect=AuthorizationError("Invalid email or password")):
        responses = [
            client.post(
                "/api/v1/auth/login",
                headers={"X-Forwarded-For": ip},
                json={"email": f"{nonce}@example.com", "password": "invalid-password"},
            )
            for _ in range(3)
        ]
    assert [response.status_code for response in responses] == [403, 403, 429]
    assert int(responses[-1].headers["Retry-After"]) > 0
