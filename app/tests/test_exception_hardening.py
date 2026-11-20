from fastapi.testclient import TestClient

from app.main import app


def test_unhandled_exception_logs_request_id_and_safe_body(client, monkeypatch, capsys):
    def fail(*args):
        raise RuntimeError("private database failure")

    monkeypatch.setattr("app.repositories.orders.OrderRepository.list_for_tenant", fail)
    from conftest import auth_header

    headers = auth_header(client, "error@example.com", "strong-password")
    headers["x-request-id"] = "error-request-123"
    with TestClient(app, raise_server_exceptions=False) as safe_client:
        response = safe_client.get("/api/v1/orders", headers=headers)
    assert response.status_code == 500
    assert response.json() == {
        "code": "internal_server_error",
        "message": "Unexpected server error",
        "details": None,
    }
    output = capsys.readouterr().out
    assert "unhandled_exception" in output
    assert "error-request-123" in output
    assert "private database failure" not in response.text
