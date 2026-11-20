from conftest import auth_header


def test_signup_ignores_privileged_role(client):
    response = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "escalate@example.com",
            "password": "strong-password",
            "full_name": "Test User",
            "role": "admin",
        },
    )
    assert response.status_code == 201
    assert response.json()["role"] == "customer"
    headers = auth_header(client, "escalate@example.com", "strong-password")
    assert client.get("/api/v1/inventory", headers=headers).status_code == 403
