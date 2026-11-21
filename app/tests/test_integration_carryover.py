from unittest.mock import patch

from test_principal_auth import register_owner


def test_login_unknown_and_wrong_password_both_verify(client):
    register_owner(client)
    responses = []
    for email in ["missing@example.com", "owner-alpha@example.com"]:
        with patch("app.services.auth.verify_password", return_value=False) as verify:
            response = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password"})
            assert verify.call_count == 1
            assert verify.call_args.args[1].startswith("$2")
        responses.append(response)
    assert responses[0].status_code == responses[1].status_code == 403
    assert responses[0].json() == responses[1].json()


def test_duplicate_warehouse_and_inventory_are_typed_conflicts(client):
    _, headers = register_owner(client)
    warehouse = {"code": "MAIN", "name": "Main warehouse", "region": "test"}
    first = client.post("/api/v1/inventory/warehouses", headers=headers, json=warehouse)
    assert first.status_code == 201
    duplicate = client.post("/api/v1/inventory/warehouses", headers=headers, json=warehouse)
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "warehouse_code_conflict"
    assert (
        client.post(
            "/api/v1/products", headers=headers, json={"sku": "STOCK", "name": "Stock", "price": "1.00"}
        ).status_code
        == 201
    )
    stock = {"sku": "STOCK", "warehouse_id": first.json()["id"], "available_quantity": 10}
    assert client.post("/api/v1/inventory", headers=headers, json=stock).status_code == 201
    duplicate = client.post("/api/v1/inventory", headers=headers, json=stock)
    assert duplicate.status_code == 409 and duplicate.json()["code"] == "inventory_conflict"
