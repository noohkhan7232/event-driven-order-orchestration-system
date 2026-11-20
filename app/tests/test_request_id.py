import re
from uuid import UUID

import pytest
from conftest import auth_header
from starlette.requests import Request
from starlette.responses import Response

from app.main import app
from app.middleware.request_context import RequestContextMiddleware
from app.models.entities import OutboxEvent


@pytest.mark.parametrize("value", ["x" * 300, "has spaces", "line\nbreak", "control\x01", "café", "", "a\r\nb"])
async def test_invalid_request_id_is_replaced(value):
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [(b"x-request-id", value.encode())]})

    async def respond(request):
        return Response()

    response = await RequestContextMiddleware(app).dispatch(request, respond)
    effective = response.headers["x-request-id"]
    assert UUID(effective).version == 4
    assert effective != value


@pytest.mark.parametrize("value", ["valid.Request-id_42", "a" * 64, None])
def test_request_id_echo_or_generation(client, value):
    response = client.get("/api/v1/system/health", headers={"x-request-id": value} if value is not None else {})
    assert response.status_code == 200
    effective = response.headers["x-request-id"]
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,64}", effective)
    if value is not None:
        assert effective == value
    else:
        assert UUID(effective).version == 4


@pytest.mark.postgres
def test_postgres_long_request_id_order_is_accepted(client, db):
    headers = auth_header(client, "request-id@example.com", "strong-password")
    response = client.post(
        "/api/v1/orders",
        headers={**headers, "Idempotency-Key": "long-header", "x-request-id": "x" * 300},
        json={"items": [{"sku": "SKU", "quantity": 1}]},
    )
    assert response.status_code == 202
    row = db.query(OutboxEvent).one()
    assert row.correlation_id == response.headers["x-request-id"]
    assert len(row.correlation_id) <= 64
