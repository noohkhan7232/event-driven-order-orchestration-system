"""Real HTTP + PostgreSQL + broker/worker/relay tenancy proof. No volumes deleted."""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.security import hash_password  # noqa: E402
from app.models.entities import User  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from scripts.e2e_reliability import Harness, compose, experiment, worker_ready  # noqa: E402


class TenancyHarness(Harness):
    def register_stores(self) -> str:
        self.stores = []
        for index in range(2):
            email = f"tenant-e2e-{self.run_id}-{index}@example.com"
            response = self.client.post(
                "/api/v1/tenants/register",
                json={
                    "business_name": f"E2E {index}",
                    "slug": f"e2e-{self.run_id}-{index}",
                    "owner_email": email,
                    "owner_password": "strong-password",
                    "owner_name": "Reviewer",
                },
            )
            assert response.status_code == 201, response.text
            tenant = response.json()
            login = self.client.post("/api/v1/auth/login", json={"email": email, "password": "strong-password"})
            assert login.status_code == 200
            jwt = {"Authorization": f"Bearer {login.json()['access_token']}"}
            product = self.client.post(
                "/api/v1/products",
                headers=jwt,
                json={
                    "sku": "SHARED",
                    "name": "Shared SKU",
                    "price": "19.99",
                    "currency": "USD",
                },
            )
            assert product.status_code == 201
            warehouse = self.client.post(
                "/api/v1/inventory/warehouses", headers=jwt, json={"code": "SHARED", "name": "Shared", "region": "test"}
            )
            assert warehouse.status_code == 201
            inventory = self.client.post(
                "/api/v1/inventory",
                headers=jwt,
                json={"sku": "SHARED", "warehouse_id": warehouse.json()["id"], "available_quantity": 20},
            )
            assert inventory.status_code == 201
            key = self.client.post(
                "/api/v1/api-keys",
                headers=jwt,
                json={"name": "E2E", "scopes": ["orders:read", "orders:write", "catalog:read", "inventory:read"]},
            )
            assert key.status_code == 201
            store = {
                "tenant": tenant,
                "jwt": jwt,
                "key": {"X-API-Key": key.json()["key"]},
                "product": product.json(),
                "stock": inventory.json(),
                "key_id": key.json()["id"],
            }
            self.stores.append(store)
        with ThreadPoolExecutor(max_workers=2) as pool:
            orders = list(pool.map(lambda store: self.place(store, "initial", quantity=3), self.stores))
        for store, order in zip(self.stores, orders, strict=True):
            store["order"] = order
            self.wait(store, order, "fulfillment_requested")
            stock = self.client.get(f"/api/v1/inventory/{store['stock']['id']}", headers=store["key"]).json()
            assert stock["available_quantity"] == 17 and stock["reserved_quantity"] == 3
        return "two registered stores; same SKU; key orders completed independently; stock=(17,3) each"

    def place(self, store, key, quantity=1, sku="SHARED") -> str:
        response = self.client.post(
            "/api/v1/orders",
            headers={**store["key"], "Idempotency-Key": key},
            json={"items": [{"sku": sku, "quantity": quantity}]},
        )
        assert response.status_code == 202, response.text
        return response.json()["id"]

    def wait(self, store, order, expected):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/v1/orders/{order}", headers=store["key"])
            assert response.status_code == 200
            body = response.json()
            if body["status"] == expected:
                return body
            if body["status"] in {"failed", "cancelled", "delivered"}:
                raise AssertionError(body)
            time.sleep(0.2)
        raise AssertionError("Order completion timeout")

    def isolation(self) -> str:
        a, b = self.stores
        assert a["order"] != b["order"]
        for credentials in [b["jwt"], b["key"]]:
            for resource, identity in [
                ("orders", a["order"]),
                ("products", a["product"]["id"]),
                ("inventory", a["stock"]["id"]),
            ]:
                assert self.client.get(f"/api/v1/{resource}/{identity}", headers=credentials).status_code == 404
                listing = self.client.get(f"/api/v1/{resource}", headers=credentials)
                assert listing.status_code == 200 and listing.json()["total"] == 1
                assert identity not in [row["id"] for row in listing.json()["items"]]
        return (
            "B JWT/key foreign order/product/inventory=404; list totals=1; A IDs absent; shared idempotency independent"
        )

    def pricing(self) -> str:
        a = self.stores[0]
        before = self.client.get(f"/api/v1/orders/{a['order']}", headers=a["key"]).json()
        assert before["total_amount"] == "59.97"
        assert (
            self.client.patch(
                f"/api/v1/products/{a['product']['id']}", headers=a["jwt"], json={"price": "25.00"}
            ).status_code
            == 200
        )
        after = self.client.get(f"/api/v1/orders/{a['order']}", headers=a["key"]).json()
        assert after["total_amount"] == "59.97" and after["items"][0]["unit_price"] == "19.99"
        unknown = self.client.post(
            "/api/v1/orders",
            headers={**a["key"], "Idempotency-Key": "unknown"},
            json={"items": [{"sku": "UNKNOWN", "quantity": 1}]},
        )
        assert unknown.status_code == 422 and unknown.json()["code"] == "unknown_or_inactive_sku"
        return "19.99 x 3=59.97; product changed to25; snapshot unchanged; unknown SKU=422"

    def key_lifecycle(self) -> str:
        a = self.stores[0]
        for expired in [False, True]:
            body = {"name": "expired" if expired else "revoked", "scopes": ["orders:read"]}
            if expired:
                body["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
            response = self.client.post("/api/v1/api-keys", headers=a["jwt"], json=body)
            assert response.status_code == 201
            row = response.json()
            if not expired:
                assert self.client.delete(f"/api/v1/api-keys/{row['id']}", headers=a["jwt"]).status_code == 204
            assert self.client.get("/api/v1/orders", headers={"X-API-Key": row["key"]}).status_code == 401
        with self.sessions.begin() as db:
            root = User(
                email=f"platform-e2e-{self.run_id}@example.com",
                full_name="E2E Platform",
                hashed_password=hash_password("strong-password"),
                role=UserRole.ADMIN,
                is_platform_admin=True,
            )
            db.add(root)
            db.flush()
            root_email = root.email
        login = self.client.post("/api/v1/auth/login", json={"email": root_email, "password": "strong-password"})
        assert login.status_code == 200
        root_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        url = f"/api/v1/admin/tenants/{a['tenant']['id']}"
        assert self.client.post(url + "/suspend", headers=root_headers).status_code == 200
        for credentials in [a["jwt"], a["key"]]:
            response = self.client.get("/api/v1/orders", headers=credentials)
            assert response.status_code == 403 and response.json()["code"] == "tenant_suspended"
        assert self.client.post(url + "/activate", headers=root_headers).status_code == 200
        for credentials in [a["jwt"], a["key"]]:
            assert self.client.get("/api/v1/orders", headers=credentials).status_code == 200
        return "revoked/expired=401; suspended JWT/key=403; activation restored both"

    def tenant_concurrency(self) -> str:
        for store in self.stores:
            assert (
                self.client.patch(
                    f"/api/v1/inventory/{store['stock']['id']}", headers=store["jwt"], json={"available_quantity": 10}
                ).status_code
                == 200
            )
        requests = [(store, index) for store in self.stores for index in range(20)]
        with ThreadPoolExecutor(max_workers=20) as pool:
            orders = list(pool.map(lambda request: self.place(request[0], f"concurrency-{request[1]}"), requests))
        deadline = time.monotonic() + 120
        results = []
        while time.monotonic() < deadline:
            results = [
                self.client.get(f"/api/v1/orders/{identity}", headers=store["key"]).json()
                for (store, _), identity in zip(requests, orders, strict=True)
            ]
            if all(row["status"] in {"failed", "fulfillment_requested"} for row in results):
                break
            time.sleep(0.2)
        for index, store in enumerate(self.stores):
            group = results[index * 20 : (index + 1) * 20]
            assert sum(row["status"] == "fulfillment_requested" for row in group) == 10
            assert sum(row["failure_reason"] == "INSUFFICIENT_INVENTORY" for row in group) == 10
            stock = self.client.get(f"/api/v1/inventory/{store['stock']['id']}", headers=store["key"]).json()
            assert stock["available_quantity"] == 0 and stock["reserved_quantity"] == 13
        return "20 concurrent orders per tenant, shared SKU: fulfilled=10 failed=10 each; stock independent"


def main() -> int:
    os.environ["FAILURE_INJECTION_ENABLED"] = "true"
    compose("up", "-d", "--no-build")
    worker_ready()
    harness = TenancyHarness()
    passed = []
    try:
        harness.initialize()
        actions = [
            ("E9 two tenant workflows", harness.register_stores),
            ("E10 isolation", harness.isolation),
            ("E11 pricing", harness.pricing),
            ("E12 key lifecycle and suspension", harness.key_lifecycle),
            ("E13 exhausted recovery", lambda: harness.transient(True)),
            ("E14 cross-tenant concurrency", harness.tenant_concurrency),
        ]
        for name, action in actions:
            passed.append(experiment(name, action))
    finally:
        os.environ["FAILURE_INJECTION_ENABLED"] = "false"
        compose("up", "-d", "--no-build")
        worker_ready()
        harness.close()
    print(f"RESULT: {sum(passed)}/{len(passed)} tenancy experiments passed", flush=True)
    print(compose("ps"), flush=True)
    return 0 if all(passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
