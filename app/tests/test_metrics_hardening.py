from uuid import uuid4

import pytest
import structlog
from conftest import auth_header
from prometheus_client import Counter
from starlette.requests import Request

from app.core.config import get_settings
from app.main import app
from app.middleware.request_context import RequestContextMiddleware


@pytest.mark.parametrize(
    "environment,token,authorization,expected",
    [
        ("local", None, None, 200),
        ("development", None, None, 200),
        ("test", None, None, 200),
        ("production", None, None, 404),
        ("production", "metrics-secret", None, 403),
        ("production", "metrics-secret", "Bearer wrong", 403),
        ("production", "metrics-secret", "Basic metrics-secret", 403),
        ("production", "metrics-secret", "Bearer metrics-secret", 200),
    ],
)
def test_metrics_access(client, environment, token, authorization, expected):
    settings = get_settings().model_copy(update={"environment": environment, "metrics_token": token})
    app.dependency_overrides[get_settings] = lambda: settings
    headers = {"Authorization": authorization} if authorization else {}
    assert client.get("/api/v1/system/metrics", headers=headers).status_code == expected


@pytest.mark.parametrize("role,expected", [("admin", 200), ("vendor", 403), ("customer", 403)])
def test_metrics_admin_jwt(client, role, expected):
    headers = auth_header(client, f"{role}@example.com", "strong-password", role)
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"environment": "production", "metrics_token": "metrics-secret"}
    )
    assert client.get("/api/v1/system/metrics", headers=headers).status_code == expected


def test_metrics_order_ids_share_route_template(client, monkeypatch):
    counter = Counter("test_route_requests", "Test", ["method", "path", "status"], registry=None)
    monkeypatch.setattr("app.middleware.request_context.REQUEST_COUNT", counter)
    headers = auth_header(client, "customer@example.com", "strong-password")
    for _ in range(2):
        assert client.get(f"/api/v1/orders/{uuid4()}", headers=headers).status_code == 404
    samples = [s for s in counter.collect()[0].samples if s.name.endswith("_total") and s.labels["method"] == "GET"]
    assert len(samples) == 1
    assert samples[0].labels["path"] == "/api/v1/orders/{order_id}"
    assert samples[0].value == 2


async def test_request_context_cleared_on_exception():
    async def fail(request):
        raise RuntimeError("boom")

    request = Request({"type": "http", "headers": []})
    middleware = RequestContextMiddleware(app)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            await middleware.dispatch(request, fail)
        assert structlog.contextvars.get_contextvars() == {}
    finally:
        structlog.contextvars.clear_contextvars()
