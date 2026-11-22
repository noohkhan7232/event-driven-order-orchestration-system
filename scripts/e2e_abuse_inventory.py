"""Real HTTP quota and mixed inventory/order races on unique local proof data."""

import json
import os
import secrets
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.entities import AuditLog, Inventory, Order  # noqa: E402
from scripts.e2e_reliability import Harness, compose, worker_ready  # noqa: E402
from scripts.verify_rate_timeline import body, require  # noqa: E402


def main():
    harness = Harness()
    override = Path(".coverage-abuse-compose-" + harness.run_id + ".yaml")

    quota_url = os.environ.get("E2E_RATE_BACKEND_URL", "")

    def configure(extra):
        override.write_text(
            json.dumps({"services": {"api": {"environment": {"RATE_LIMIT_REDIS_URL": quota_url, **extra}}}})
        )
        compose("-f", "docker-compose.yml", "-f", str(override), "up", "-d", "--no-build", "api")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if harness.client.get("/api/v1/system/health").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.25)
        raise RuntimeError("API readiness timeout")

    try:
        configure({})
        email, password = "abuse-" + harness.run_id + "@example.com", secrets.token_urlsafe(24)
        tenant = body(
            harness.client.post(
                "/api/v1/tenants/register",
                json={
                    "business_name": "Abuse and race proof",
                    "slug": "checks-" + harness.run_id,
                    "owner_email": email,
                    "owner_password": password,
                    "owner_name": "Proof owner",
                },
            ),
            201,
        )
        harness.tenant_id = UUID(tenant["id"])
        login = body(harness.client.post("/api/v1/auth/login", json={"email": email, "password": password}), 200)
        harness.headers = {"Authorization": "Bearer " + login["access_token"]}
        keys = [
            body(
                harness.client.post(
                    "/api/v1/api-keys", headers=harness.headers, json={"name": "Quota proof", "scopes": ["orders:read"]}
                ),
                201,
            )["key"]
            for _ in range(2)
        ]
        configure(
            {
                "RATE_LIMIT_LOGIN_IP_LIMIT": "3",
                "RATE_LIMIT_LOGIN_EMAIL_LIMIT": "2",
                "RATE_LIMIT_REGISTER_LIMIT": "2",
                "RATE_LIMIT_AUTHENTICATED_LIMIT": "3",
                "RATE_LIMIT_REDIS_URL": quota_url.rsplit("/", 1)[0] + "/1" if quota_url else "",
            }
        )
        attempts = [
            harness.client.post(
                "/api/v1/auth/login",
                json={"email": "absent-" + harness.run_id + "@example.com", "password": "invalid-password"},
            )
            for _ in range(3)
        ]
        require([r.status_code for r in attempts] == [403, 403, 429], "Login/email quota mismatch")
        spoofed = harness.client.post(
            "/api/v1/auth/login",
            headers={"X-Forwarded-For": "203.0.113.99"},
            json={"email": "another-" + harness.run_id + "@example.com", "password": "invalid-password"},
        )
        require(spoofed.status_code == 429, "Forwarded IP bypassed socket quota at TRUSTED_PROXY_COUNT=0")
        registration = [harness.client.post("/api/v1/tenants/register", json={}) for _ in range(3)]
        require([r.status_code for r in registration] == [422, 422, 429], "Registration quota mismatch")
        requests = [harness.client.get("/api/v1/orders", headers={"X-API-Key": keys[0]}) for _ in range(4)]
        require([r.status_code for r in requests] == [200, 200, 200, 429], "Per-key quota mismatch")
        require(
            harness.client.get("/api/v1/orders", headers={"X-API-Key": keys[1]}).status_code == 200,
            "One API key consumed another key's allowance",
        )
        for limited in (attempts[-1], spoofed, registration[-1], requests[-1]):
            require(
                int(limited.headers["Retry-After"]) > 0 and limited.headers["X-RateLimit-Remaining"] == "0",
                "Quota headers missing",
            )
        print(
            "PASS E28: login email/IP and registration quotas, forged XFF ignored, "
            "independent API keys, strict429/Retry-After headers",
            flush=True,
        )

        configure({})
        worker_ready()
        sku = "RACE-" + harness.run_id
        body(
            harness.client.post(
                "/api/v1/products",
                headers=harness.headers,
                json={"sku": sku, "name": "Race product", "price": "1.00", "currency": "INR"},
            ),
            201,
        )
        warehouse = body(
            harness.client.post(
                "/api/v1/inventory/warehouses",
                headers=harness.headers,
                json={"code": "RACE", "name": "Race", "region": "test"},
            ),
            201,
        )
        stock = body(
            harness.client.post(
                "/api/v1/inventory",
                headers=harness.headers,
                json={"sku": sku, "warehouse_id": warehouse["id"], "available_quantity": 20},
            ),
            201,
        )
        actions = [("order", 0)] * 30 + [("adjust", 1)] * 10 + [("adjust", -1)] * 5

        def act(action):
            kind, delta = action
            if kind == "order":
                response = harness.client.post(
                    "/api/v1/orders",
                    headers={**harness.headers, "Idempotency-Key": uuid4().hex},
                    json={"items": [{"sku": sku, "quantity": 1}]},
                )
                return "order", body(response, 202)["id"]
            response = harness.client.post(
                f"/api/v1/inventory/{stock['id']}/adjustments",
                headers=harness.headers,
                json={"delta": delta, "reason": "race-proof"},
            )
            if response.status_code == 422:
                require(response.json()["code"] == "negative_stock", "Unexpected adjustment rejection")
                return "rejected", 0
            body(response, 200)
            return "accepted", delta

        with ThreadPoolExecutor(max_workers=20) as pool:
            outcomes = list(pool.map(act, actions))
        identities = [UUID(value) for kind, value in outcomes if kind == "order"]
        accepted = [value for kind, value in outcomes if kind == "accepted"]
        deadline = time.monotonic() + 180
        while True:
            with harness.sessions() as db:
                orders = list(db.scalars(select(Order).where(Order.id.in_(identities))))
            if all(row.status.value in {"failed", "fulfillment_requested"} for row in orders):
                break
            require(time.monotonic() < deadline, "Race order completion timeout")
            time.sleep(0.25)
        fulfilled = sum(row.status.value == "fulfillment_requested" for row in orders)
        require(
            all(row.failure_reason == "INSUFFICIENT_INVENTORY" for row in orders if row.status.value == "failed"),
            "Unexpected race failure",
        )
        with harness.sessions() as db:
            current = db.get(Inventory, UUID(stock["id"]))
            require(
                current.available_quantity + current.reserved_quantity == 20 + sum(accepted),
                "Race stock was not conserved",
            )
            require(
                current.reserved_quantity == fulfilled and current.available_quantity >= 0, "Reservation count mismatch"
            )
            audits = db.scalars(
                select(AuditLog).where(AuditLog.entity_id == current.id, AuditLog.action == "inventory_adjusted")
            ).all()
            require(
                len(audits) == len(accepted) and sum(row.payload["delta"] for row in audits) == sum(accepted),
                "Accepted adjustment audit mismatch",
            )
        print(
            f"PASS E30:30 orders+15 adjustments, accepted_delta={sum(accepted)}, "
            f"fulfilled={fulfilled}, stock conserved and audits exact",
            flush=True,
        )
    finally:
        try:
            compose("up", "-d", "--no-build", "api", "worker")
            worker_ready()
            runtime = compose(
                "exec",
                "-T",
                "api",
                "python",
                "-c",
                "import json; from app.core.config import get_settings; s=get_settings(); "
                "print(json.dumps({'login_ip':s.rate_limit_login_ip_limit,"
                "'login_email':s.rate_limit_login_email_limit,"
                "'register':s.rate_limit_register_limit,'authenticated':s.rate_limit_authenticated_limit,"
                "'trusted_proxies':s.trusted_proxy_count,'shared_rate_backend':bool(s.rate_limit_redis_url)}))",
            )
            values = json.loads(runtime)
            require(
                (
                    values["login_ip"],
                    values["login_email"],
                    values["register"],
                    values["authenticated"],
                    values["trusted_proxies"],
                )
                == (10, 5, 5, 600, 0),
                "Quota defaults not restored",
            )
            print("RESTORED quota runtime " + runtime, flush=True)
        finally:
            override.unlink(missing_ok=True)
            harness.client.close()
            harness.engine.dispose()


if __name__ == "__main__":
    main()
