"""Local-stack smoke for tasks 3B.2/3B.3; leaves uniquely named demo rows.

Uses the existing E2E URL/database configuration. Run with the local mock providers.
No API keys/passwords/tokens or response bodies are printed.
"""

import secrets
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.events.contracts import EventNames  # noqa: E402
from app.models.entities import OrderTimeline, OutboxEvent, ProcessedEvent  # noqa: E402
from scripts.e2e_reliability import Harness  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def body(response: httpx.Response, expected: int) -> dict[str, Any]:
    require(response.status_code == expected, f"Unexpected HTTP status: {response.status_code}, expected {expected}")
    return response.json()


def main() -> None:
    harness = Harness()
    try:
        client = harness.client
        deadline = time.monotonic() + 30
        while True:
            try:
                health = client.get("/api/v1/system/health")
                if health.status_code == 200:
                    break
            except httpx.TransportError:
                pass
            require(time.monotonic() < deadline, "Local API did not become ready")
            time.sleep(0.2)
        require("X-RateLimit-Limit" in health.headers, "Public rate-limit headers missing")
        nonce = harness.run_id
        email = f"rate-timeline-{nonce}@example.com"
        password = secrets.token_urlsafe(24)
        tenant = body(
            client.post(
                "/api/v1/tenants/register",
                json={
                    "business_name": "Local timeline verification",
                    "slug": f"verify-{nonce}",
                    "owner_email": email,
                    "owner_password": password,
                    "owner_name": "Verification Owner",
                },
            ),
            201,
        )
        login = body(client.post("/api/v1/auth/login", json={"email": email, "password": password}), 200)
        auth = {"Authorization": f"Bearer {login['access_token']}"}
        sku = f"VERIFY-{nonce}"
        body(
            client.post(
                "/api/v1/products",
                headers=auth,
                json={"sku": sku, "name": "Verification product", "price": "1.00", "currency": "INR"},
            ),
            201,
        )
        warehouse = body(
            client.post(
                "/api/v1/inventory/warehouses",
                headers=auth,
                json={"code": "MAIN", "name": "Verification", "region": "test"},
            ),
            201,
        )
        body(
            client.post(
                "/api/v1/inventory",
                headers=auth,
                json={"sku": sku, "warehouse_id": warehouse["id"], "available_quantity": 10},
            ),
            201,
        )
        headers = {**auth, "Idempotency-Key": f"verify-{nonce}"}
        payload = {"items": [{"sku": sku, "quantity": 1}]}
        created = body(client.post("/api/v1/orders", headers=headers, json=payload), 202)
        replay = body(client.post("/api/v1/orders", headers=headers, json=payload), 202)
        require(created["id"] == replay["id"], "Idempotent replay changed order ID")
        identity = UUID(created["id"])
        deadline = time.monotonic() + 90
        while True:
            response = client.get(f"/api/v1/orders/{identity}", headers=auth)
            order = body(response, 200)
            with harness.sessions() as db:
                timeline = list(
                    db.scalars(
                        select(OrderTimeline).where(OrderTimeline.order_id == identity).order_by(OrderTimeline.id)
                    )
                )
                intents = list(
                    db.scalars(
                        select(OutboxEvent).where(
                            OutboxEvent.aggregate_id == identity,
                            OutboxEvent.event_type == EventNames.ORDER_STATUS_CHANGED,
                        )
                    )
                )
                consumed = set(
                    db.scalars(
                        select(ProcessedEvent.event_id).where(
                            ProcessedEvent.consumer == "order_workflow",
                            ProcessedEvent.event_id.in_([row.event_id for row in intents]),
                        )
                    )
                )
            if order["status"] == "fulfillment_requested" and len(consumed) == 4:
                break
            require(order["status"] != "failed", "Verification order failed")
            require(time.monotonic() < deadline, "Timeline/status consumer completion timed out")
            time.sleep(0.2)
        require("X-RateLimit-Limit" in response.headers, "Authenticated quota headers missing")
        require(
            [row.to_status for row in timeline]
            == ["created", "inventory_reserved", "payment_pending", "paid", "fulfillment_requested"],
            "Unexpected timeline sequence",
        )
        require(len(intents) == 4, "Unexpected status intent count")
        require(all(str(row.tenant_id) == tenant["id"] for row in timeline + intents), "Timeline tenant mismatch")
        allowed = {"order_id", "from", "to", "reason", "tenant_id"}
        require(
            all(set(row.payload["payload"]) == allowed for row in intents),
            "Status event payload is not the allowed public shape",
        )
        body(client.post("/api/v1/orders", headers=headers, json=payload), 202)
        with harness.sessions() as db:
            rows = list(db.scalars(select(OrderTimeline.id).where(OrderTimeline.order_id == identity)))
        require(len(rows) == 5, "Replay added timeline rows")
        print(
            "PASS local Docker smoke: public/auth quota headers; 5 ordered timeline rows; "
            "4 status intents/consumer markers; replay unchanged"
        )
    finally:
        harness.close()


if __name__ == "__main__":
    main()
