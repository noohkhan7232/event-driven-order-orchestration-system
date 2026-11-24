"""Real Docker E19/E22-E27/E29; synthetic buyers, no remote providers or deleted volumes."""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any
from uuid import UUID, uuid4

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth.security import hash_password  # noqa: E402
from app.models.entities import (  # noqa: E402
    AuditLog,
    Notification,
    OrderTimeline,
    Payment,
    Shipment,
    User,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.models.enums import PaymentStatus, UserRole  # noqa: E402
from scripts.e2e_reliability import Harness, compose, experiment, worker_ready  # noqa: E402
from scripts.verify_rate_timeline import body, require  # noqa: E402

CASES = ("E19", "E22", "E23", "E24", "E25", "E26", "E27", "E29")


def wait(check: Callable[[], Any], label: str, timeout: float = 120) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.25)
    raise RuntimeError("Timed out: " + label)


def verify_received(rows: list[dict[str, Any]], secret: str) -> list[dict[str, Any]]:
    """Independent of the application's signing code; validate every HTTP attempt."""
    envelopes = []
    for row in rows:
        headers = {key.lower(): value for key, value in row["headers"].items()}
        parts = dict(part.split("=", 1) for part in headers["x-oos-signature"].split(","))
        stamp = parts["t"]
        digest = hmac.new(secret.encode(), (stamp + "." + row["body"]).encode(), hashlib.sha256).hexdigest()
        require(hmac.compare_digest(digest, parts["v1"]), "Webhook signature mismatch")
        envelope = json.loads(row["body"])
        require(stamp == headers["x-oos-timestamp"], "Timestamp header mismatch")
        require(envelope["id"] == headers["x-oos-event-id"], "Signed event identity mismatch")
        require(envelope["type"] == headers["x-oos-event-type"], "Signed event type mismatch")
        envelopes.append(envelope)
    return envelopes


class SellerHarness(Harness):
    def __init__(self) -> None:
        super().__init__()
        self.endpoint_ids: list[UUID] = []
        self.platform_user_id: UUID | None = None
        self.sink = httpx.Client(
            base_url=os.environ.get("E2E_SINK_URL", "http://127.0.0.1:8090"), timeout=10, trust_env=False
        )
        self.mail = httpx.Client(
            base_url=os.environ.get("E2E_MAILPIT_URL", "http://127.0.0.1:8025"), timeout=10, trust_env=False
        )

    def initialize(self) -> None:
        self.store_name = "P13-" + self.run_id
        email = f"p13-{self.run_id}@example.com"
        store = body(
            self.client.post(
                "/api/v1/tenants/register",
                json={
                    "business_name": self.store_name,
                    "slug": "p13-" + self.run_id,
                    "owner_email": email,
                    "owner_password": "local-proof-password",
                    "owner_name": "Reviewer",
                },
            ),
            201,
        )
        login = body(
            self.client.post("/api/v1/auth/login", json={"email": email, "password": "local-proof-password"}), 200
        )
        self.headers = {"Authorization": "Bearer " + login["access_token"]}
        self.tenant_id = UUID(store["id"])
        warehouse = body(
            self.client.post(
                "/api/v1/inventory/warehouses",
                headers=self.headers,
                json={"code": "MAIN", "name": "Proof warehouse", "region": "local"},
            ),
            201,
        )
        self.warehouse_id = UUID(warehouse["id"])
        body(
            self.client.patch("/api/v1/settings/shipping", headers=self.headers, json={"shipping_provider": "manual"}),
            200,
        )

    def place(self, sku: str, *, method: str = "prepaid", email: str | None = None) -> str:
        payload: dict[str, Any] = {"items": [{"sku": sku, "quantity": 1}], "payment_method": method}
        if email:
            payload.update(buyer_email=email, buyer_name="PRIVATE_BUYER", buyer_phone="+919999999999")
        return body(
            self.client.post("/api/v1/orders", headers={**self.headers, "Idempotency-Key": uuid4().hex}, json=payload),
            202,
        )["id"]

    def order(self, identity: str) -> dict[str, Any]:
        return body(self.client.get(f"/api/v1/orders/{identity}", headers=self.headers), 200)

    def reach(self, identity: str, status: str) -> dict[str, Any]:
        return wait(lambda: value if (value := self.order(identity))["status"] == status else None, status)

    def event(self, identity: str, status: str) -> None:
        shipment = self.order(identity)["shipments"][0]["id"]
        body(
            self.client.post(f"/api/v1/shipments/{shipment}/events", headers=self.headers, json={"status": status}), 200
        )

    def ship(self, identity: str) -> None:
        shipment = self.order(identity)["shipments"][0]["id"]
        body(
            self.client.patch(
                f"/api/v1/shipments/{shipment}",
                headers=self.headers,
                json={
                    "carrier": "Seller courier",
                    "tracking_number": "P13-" + uuid4().hex,
                    "tracking_url": "https://example.com/track",
                    "estimated_delivery": "2026-12-01",
                },
            ),
            200,
        )
        self.event(identity, "in_transit")
        self.reach(identity, "shipped")

    def delivered(self, identity: str) -> dict[str, Any]:
        self.event(identity, "delivered")
        return self.reach(identity, "delivered")

    def shipping(self) -> str:
        sku = self.seed("cod")
        identity = self.place(sku, method="cod")
        self.reach(identity, "fulfillment_requested")
        self.event(identity, "delayed")
        require(self.order(identity)["status"] == "fulfillment_requested", "Delay moved order")
        self.ship(identity)
        self.delivered(identity)
        self.event(identity, "in_transit")
        self.event(identity, "delivered")
        wait(lambda: self.order(identity)["payment_status"] == "collected", "COD collection")
        with self.sessions() as db:
            payments = list(db.scalars(select(Payment).where(Payment.order_id == UUID(identity))))
            require(len(payments) == 1 and payments[0].status == PaymentStatus.CAPTURED, "COD capture count")
            require(
                db.query(OrderTimeline).filter_by(order_id=UUID(identity), to_status="delivered").count() == 1,
                "Duplicate delivered transition",
            )
            require(
                db.query(AuditLog).filter_by(entity_id=payments[0].id, action="cod_collected").count() == 1,
                "COD audit count",
            )
        require(self.order(identity)["status"] == "delivered" and self.stock(sku) == (9, 1), "Stale shipping effect")
        return "manual metadata/events; stale/duplicate harmless; one DELIVERED and one COD collection/audit"

    def endpoint(self, events: list[str]) -> tuple[str, str]:
        row = body(
            self.client.post(
                "/api/v1/webhook-endpoints",
                headers=self.headers,
                json={"url": "http://webhook-sink:8080/receive", "events": events},
            ),
            201,
        )
        self.endpoint_ids.append(UUID(row["id"]))
        return row["id"], row["signing_secret"]

    def deactivate(self, endpoint: str) -> None:
        require(
            self.client.delete(f"/api/v1/webhook-endpoints/{endpoint}", headers=self.headers).status_code == 204,
            "Endpoint deactivation failed",
        )

    def receipts(self) -> list[dict[str, Any]]:
        return body(self.sink.get("/received"), 200)["received"]

    def deliveries(self, endpoint: str) -> list[dict[str, Any]]:
        return body(self.client.get(f"/api/v1/webhook-endpoints/{endpoint}/deliveries", headers=self.headers), 200)[
            "items"
        ]

    def outbound(self) -> str:
        body(self.sink.post("/config", json={"failures": 0, "clear": True}), 200)
        expected = ["order.created", "order.paid", "order.shipped", "order.delivered"]
        endpoint, secret = self.endpoint(expected)
        identity = self.place(self.seed("hooks"), email=f"p13-hooks-{self.run_id}@example.com")
        self.reach(identity, "fulfillment_requested")
        self.ship(identity)
        self.delivered(identity)
        wait(
            lambda: (
                len(self.deliveries(endpoint)) == 4
                and all(row["status"] == "delivered" for row in self.deliveries(endpoint))
            ),
            "four delivered webhooks",
        )
        rows = self.receipts()
        envelopes = verify_received(rows, secret)
        require(Counter(row["type"] for row in envelopes) == Counter(expected), "Webhook event catalog/count")
        by_type = {row["type"]: row for row in envelopes}
        require(
            [by_type[name]["created_at"] for name in expected]
            == sorted(by_type[name]["created_at"] for name in expected),
            "Business event sequence",
        )
        for private in (
            "buyer_email",
            "buyer_phone",
            "buyer_name",
            "PRIVATE_BUYER",
            f"p13-hooks-{self.run_id}@example.com",
            "+919999999999",
            secret,
        ):
            require(private not in json.dumps(envelopes), "Private webhook field")
        self.deactivate(endpoint)
        return (
            "four logical lifecycle events; every received signature independently verified; "
            "delivered logs, no buyer PII"
        )

    def retries(self) -> str:
        body(self.sink.post("/config", json={"failures": 2, "clear": True}), 200)
        endpoint, secret = self.endpoint(["ping"])
        base = f"/api/v1/webhook-endpoints/{endpoint}"
        first = body(self.client.post(base + "/test", headers=self.headers), 202)
        wait(
            lambda: any(row["id"] == first["id"] and row["status"] == "delivered" for row in self.deliveries(endpoint)),
            "retry success",
        )
        rows = self.receipts()
        verify_received(rows, secret)
        require([row["status"] for row in rows] == [503, 503, 204], "Transient retry sequence")
        require(len({json.loads(row["body"])["id"] for row in rows}) == 1, "Retry changed event ID")
        body(self.sink.post("/config", json={"failures": 100, "clear": True}), 200)
        second = body(self.client.post(base + "/test", headers=self.headers), 202)
        wait(
            lambda: any(row["id"] == second["id"] and row["status"] == "dead" for row in self.deliveries(endpoint)),
            "exhausted delivery",
        )
        require(len(self.receipts()) == 3, "Exhausted attempt budget")
        body(self.sink.post("/config", json={"failures": 0}), 200)
        replay = body(self.client.post(base + f"/deliveries/{second['id']}/replay", headers=self.headers), 200)
        require(replay["event_id"] == second["event_id"] and replay["replay_count"] == 1, "Replay changed identity")
        wait(
            lambda: any(
                row["id"] == second["id"] and row["status"] == "delivered" for row in self.deliveries(endpoint)
            ),
            "replay success",
        )
        rows = self.receipts()
        verify_received(rows, secret)
        require([row["status"] for row in rows] == [503, 503, 503, 204], "Dead/replay attempt sequence")
        require({json.loads(row["body"])["id"] for row in rows} == {second["event_id"]}, "Replay ID drift")
        self.deactivate(endpoint)
        return "503/503/204; exhaustion dead after3; replay same event ID/replay_count1; one success each"

    def ssrf(self) -> str:
        urls = [
            "https://localhost",
            "https://127.0.0.1",
            "https://169.254.169.254",
            "https://10.0.0.1",
            "https://172.16.0.1",
            "https://192.168.1.1",
            "https://100.64.0.1",
            "https://[::1]",
            "https://[fd00::1]",
            "https://[::ffff:127.0.0.1]",
            "https://2130706433",
            "https://0x7f000001",
            "https://0177.0.0.1",
            "http://8.8.8.8",
        ]
        for url in urls:
            response = self.client.post(
                "/api/v1/webhook-endpoints", headers=self.headers, json={"url": url, "events": ["ping"]}
            )
            require(
                response.status_code == 400 and response.json()["code"] == "unsafe_webhook_url",
                "Strict SSRF rejection failed",
            )
        return "14 real API rejections: localhost, metadata/private/CGNAT, IPv6/mapped, encoded IPs and non-HTTPS"

    def messages(self, email: str) -> list[dict[str, Any]]:
        rows = body(self.mail.get("/api/v1/messages"), 200)["messages"]
        return [row for row in rows if any(to["Address"] == email for to in row["To"])]

    def notification_check(self, identity: str, email: str, expected: dict[str, str]) -> None:
        def ready() -> list[Notification] | None:
            with self.sessions() as db:
                rows = list(db.scalars(select(Notification).where(Notification.order_id == UUID(identity))))
                return rows if len(rows) == len(expected) and all(row.status == "sent" for row in rows) else None

        rows = wait(ready, "notification journal sent")
        require({row.template for row in rows} == set(expected), "Notification trigger set")
        messages = wait(lambda: self.messages(email), "Mailpit messages")
        require(len(messages) == len(expected), "Mailpit exact message count")
        require(
            Counter(message["Subject"].split(": ", 1)[1] for message in messages) == Counter(expected.values()),
            "Mailpit template counts",
        )

    def notifications(self) -> str:
        email = f"p13-happy-{self.run_id}@example.com"
        identity = self.place(self.seed("email"), email=email)
        self.reach(identity, "fulfillment_requested")
        compose("restart", "worker")
        worker_ready()
        self.ship(identity)
        self.delivered(identity)
        self.notification_check(
            identity,
            email,
            {
                "order_confirmed": "Order confirmed",
                "order_shipped": "Order shipped",
                "order_delivered": "Order delivered",
            },
        )
        cancel_email = f"p13-cancel-{self.run_id}@example.com"
        cancelled = self.place(self.seed("email-cancel"), email=cancel_email)
        self.reach(cancelled, "fulfillment_requested")
        body(self.client.post(f"/api/v1/orders/{cancelled}/cancel", headers=self.headers), 200)
        self.wait_refunds(cancelled)
        self.notification_check(
            cancelled,
            cancel_email,
            {
                "order_confirmed": "Order confirmed",
                "order_cancelled": "Order cancelled",
                "refund_processed": "Refund processed",
            },
        )
        fail_email = f"p13-failed-{self.run_id}@example.com"
        failed = self.place(self.seed("email-fail", quantity=0), email=fail_email)
        self.reach(failed, "failed")
        self.notification_check(failed, fail_email, {"order_failed": "Order could not be completed"})
        body(
            self.client.patch(
                "/api/v1/settings/notifications", headers=self.headers, json={"notifications_enabled": False}
            ),
            200,
        )
        skipped_email = f"p13-optout-{self.run_id}@example.com"
        skipped = self.place(self.seed("email-optout"), email=skipped_email)
        self.reach(skipped, "fulfillment_requested")
        self.ship(skipped)
        self.delivered(skipped)

        def skip_ready() -> bool:
            with self.sessions() as db:
                rows = list(db.scalars(select(Notification).where(Notification.order_id == UUID(skipped))))
                return len(rows) == 3 and all(row.status == "skipped" for row in rows)

        wait(skip_ready, "opt-out journal")
        compose(
            "exec",
            "-T",
            "worker",
            "python",
            "-c",
            "from app.workers.tasks import send_notifications; send_notifications.delay(); send_notifications.delay()",
        )
        time.sleep(3)
        require(
            len(self.messages(email)) == 3
            and len(self.messages(cancel_email)) == 3
            and len(self.messages(fail_email)) == 1
            and not self.messages(skipped_email),
            "Notification redelivery/opt-out",
        )
        body(
            self.client.patch(
                "/api/v1/settings/notifications", headers=self.headers, json={"notifications_enabled": True}
            ),
            200,
        )
        return (
            "Mailpit exactly1 per trigger across6 templates; graceful worker restart mid-order "
            "and repeat polls no resend; opt-out0"
        )

    def tracking(self) -> str:
        identity = self.place(self.seed("track"), email=f"p13-private-{self.run_id}@example.com")
        self.reach(identity, "fulfillment_requested")
        token = self.order(identity)["tracking_token"]
        path = "/api/v1/public/track/" + token
        response = self.client.get(path)
        value = body(response, 200)
        require(
            response.headers["cache-control"] == "no-store" and response.headers["referrer-policy"] == "no-referrer",
            "Tracking privacy headers",
        )
        for forbidden in (
            "buyer_email",
            "buyer_name",
            "buyer_phone",
            "PRIVATE_BUYER",
            "p13-private-",
            "unit_price",
            "customer_id",
            "tenant_id",
            "payments",
            "failure_reason",
            "tracking_token",
            "total",
        ):
            require(forbidden not in json.dumps(value), "Tracking private field")
        rotated = body(self.client.post(f"/api/v1/orders/{identity}/tracking/rotate", headers=self.headers), 200)
        require(self.client.get(path).status_code == 404, "Old tracking link accepted")
        body(self.client.get("/api/v1/public/track/" + rotated["tracking_token"]), 200)
        limited = self.client.get(path)
        require(limited.status_code == 429 and int(limited.headers["retry-after"]) > 0, "Tracking quota")
        return "safe public JSON/headers; rotate invalidates old link; configured3-request quota returns429/Retry-After"

    def stream(
        self, path: str, headers: dict[str, str], stop: str | None = None, signal: Event | None = None
    ) -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = []
        current: dict[str, Any] = {}
        with httpx.Client(base_url=str(self.client.base_url), timeout=30, trust_env=False) as client:
            with client.stream("GET", path, headers=headers) as response:
                require(
                    response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream"),
                    "SSE response contract",
                )
                for line in response.iter_lines():
                    if not line:
                        if "data" in current:
                            current["data"] = json.loads(current["data"])
                            current["id"] = int(current["id"])
                            require(current["event"] == "order.status_changed", "SSE event type")
                            frames.append(current)
                            if signal and current["data"]["to"] == "fulfillment_requested":
                                signal.set()
                            if stop and current["data"]["to"] == stop:
                                return frames
                        current = {}
                    elif not line.startswith(":"):
                        key, _, value = line.partition(": ")
                        current[key] = value
        return frames

    def streams(self, foreign: "SellerHarness") -> str:
        identity = self.place(self.seed("stream"))
        self.reach(identity, "fulfillment_requested")
        ready = Event()
        path = f"/api/v1/orders/stream?order_id={identity}&last_event_id=0"
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.stream, path, self.headers, "shipped", ready)
            require(ready.wait(15), "Live SSE did not reach fulfillment")
            self.ship(identity)
            initial = future.result(timeout=30)
        require(
            [frame["data"]["to"] for frame in initial]
            == ["created", "inventory_reserved", "payment_pending", "paid", "fulfillment_requested", "shipped"],
            "SSE ordered status sequence",
        )
        ids = [frame["id"] for frame in initial]
        require(ids == sorted(set(ids)), "SSE duplicate/out-of-order IDs")
        self.delivered(identity)
        resumed = self.stream(
            f"/api/v1/orders/stream?order_id={identity}", {**self.headers, "Last-Event-ID": str(ids[-1])}, "delivered"
        )
        with self.sessions() as db:
            expected = list(
                db.scalars(
                    select(OrderTimeline.id).where(OrderTimeline.order_id == UUID(identity)).order_by(OrderTimeline.id)
                )
            )
        require(ids + [frame["id"] for frame in resumed] == expected, "SSE resume lost/duplicated IDs")
        token = self.order(identity)["tracking_token"]
        public = self.stream(f"/api/v1/public/track/{token}/stream?last_event_id=0", {}, "delivered")
        require([frame["id"] for frame in public] == expected, "Public SSE history")
        require(
            all(frame["data"]["to"] in {"confirmed", "processing", "shipped", "delivered"} for frame in public),
            "Public SSE internal status",
        )
        other = self.stream("/api/v1/orders/stream?last_event_id=0", foreign.headers)
        require(not other, "Foreign tenant stream leaked events")
        return (
            "real HTTP live sequence; disconnect/reconnect Last-Event-ID exact timeline; "
            "friendly public stream; foreign0"
        )

    def recovery(self, foreign: "SellerHarness", configure: Callable[..., None]) -> str:
        configure(FAILURE_INJECTION_ENABLED="true")
        first_sku = self.seed("replay", prefix="FAIL-TRANSIENT-EXHAUST-")
        second_sku = self.seed("discard", prefix="FAIL-TRANSIENT-EXHAUST-")
        first, second = self.place(first_sku), self.place(second_sku)
        foreign_sku = foreign.seed("foreign-failure", prefix="FAIL-TRANSIENT-EXHAUST-")
        third = foreign.place(foreign_sku)
        for identity in (first, second):
            require(
                self.reach(identity, "failed")["failure_reason"] == "RETRIES_EXHAUSTED", "Expected technical failure"
            )
        require(foreign.reach(third, "failed")["failure_reason"] == "RETRIES_EXHAUSTED", "Foreign technical failure")
        listing = body(self.client.get("/api/v1/failed-jobs?status=failed", headers=self.headers), 200)["items"]
        selected = {row["aggregate_id"]: row for row in listing if row["aggregate_id"] in {first, second}}
        require(len(selected) == 2, "Owner failed jobs missing")
        for row in selected.values():
            require(
                self.client.get(f"/api/v1/failed-jobs/{row['id']}", headers=foreign.headers).status_code == 404,
                "Foreign failed job visible",
            )
        other = body(self.client.get("/api/v1/failed-jobs", headers=foreign.headers), 200)
        require({row["aggregate_id"] for row in other["items"]} == {third}, "Foreign failed job listing")
        foreign_job = other["items"][0]
        require(
            self.client.get(f"/api/v1/failed-jobs/{foreign_job['id']}", headers=self.headers).status_code == 404,
            "Owner saw foreign failed job",
        )
        root_password = uuid4().hex
        with self.sessions.begin() as db:
            root = User(
                tenant_id=None,
                is_platform_admin=True,
                role=UserRole.ADMIN,
                email=f"p13-root-{self.run_id}@example.com",
                full_name="Proof platform",
                hashed_password=hash_password(root_password),
            )
            db.add(root)
            db.flush()
            self.platform_user_id = root.id
        admin = body(self.client.post("/api/v1/auth/login", json={"email": root.email, "password": root_password}), 200)
        admin_headers = {"Authorization": "Bearer " + admin["access_token"]}
        platform = body(
            self.client.get(
                "/api/v1/admin/failed-jobs", headers=admin_headers, params={"tenant_id": str(self.tenant_id)}
            ),
            200,
        )
        require({row["id"] for row in selected.values()} <= {row["id"] for row in platform["items"]}, "Platform jobs")
        require(foreign_job["id"] not in {row["id"] for row in platform["items"]}, "Platform tenant filter")
        global_jobs = body(
            self.client.get("/api/v1/admin/failed-jobs", headers=admin_headers, params={"limit": 100}), 200
        )
        require(
            {row["id"] for row in selected.values()} | {foreign_job["id"]}
            <= {row["id"] for row in global_jobs["items"]},
            "Platform cross-tenant visibility",
        )
        configure(FAILURE_INJECTION_ENABLED="false")
        job = selected[first]
        body(self.client.post(f"/api/v1/failed-jobs/{job['id']}/replay", headers=self.headers), 202)
        again = self.client.post(f"/api/v1/failed-jobs/{job['id']}/replay", headers=self.headers)
        require(again.status_code == 409 and again.json()["code"] == "job_already_replayed", "Double replay accepted")
        self.reach(first, "fulfillment_requested")
        discard = selected[second]
        require(
            self.client.post(f"/api/v1/failed-jobs/{discard['id']}/discard", headers=self.headers).status_code == 204,
            "Discard HTTP status",
        )
        require(
            self.client.post(f"/api/v1/failed-jobs/{discard['id']}/replay", headers=self.headers).status_code == 409,
            "Discard replay accepted",
        )
        with self.sessions() as db:
            payments = list(db.scalars(select(Payment).where(Payment.order_id == UUID(first))))
            require(
                len(payments) == 2 and {row.provider_status for row in payments} == {"abandoned", "captured"},
                "Recovery numbered payment effects",
            )
            require(db.query(Shipment).filter_by(order_id=UUID(first)).count() == 1, "Recovery shipment count")
            for row, action in ((job, "failed_job_replayed"), (discard, "failed_job_discarded")):
                audits = db.query(AuditLog).filter_by(entity_id=UUID(row["id"]), action=action).all()
                require(len(audits) == 1 and audits[0].actor_id is not None, "Recovery actor audit")
        require(self.stock(first_sku) == (9, 1) and self.stock(second_sku) == (10, 0), "Recovery stock")
        require(foreign.stock(foreign_sku) == (10, 0), "Foreign failure compensation")
        return (
            "real exhausted retries; owner/foreign/platform HTTP; replay once, "
            "exact attempts/shipment/stock; discard+actor audits"
        )

    def cleanup(self) -> None:
        # Remove only this proof's temporary encrypted endpoint credentials and
        # dependent delivery fixtures before retiring their ephemeral key.
        retired = False
        with self.sessions.begin() as db:
            if self.platform_user_id is not None:
                principal = db.get(User, self.platform_user_id)
                if principal is not None:
                    require(
                        principal.tenant_id is None
                        and principal.is_platform_admin
                        and principal.email == f"p13-root-{self.run_id}@example.com"
                        and principal.full_name == "Proof platform",
                        "Unexpected platform fixture",
                    )
                    # Keep any audit references while revoking the proof account.
                    principal.is_active = False
                    retired = True
            if self.endpoint_ids:
                db.execute(
                    delete(WebhookDelivery).where(
                        WebhookDelivery.tenant_id == self.tenant_id, WebhookDelivery.endpoint_id.in_(self.endpoint_ids)
                    )
                )
                db.execute(
                    delete(WebhookEndpoint).where(
                        WebhookEndpoint.tenant_id == self.tenant_id, WebhookEndpoint.id.in_(self.endpoint_ids)
                    )
                )
        if retired:
            print("PASS platform fixture cleanup: temporary principal disabled", flush=True)
        self.sink.close()
        self.mail.close()
        self.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("all", *CASES), default="all")
    selected = parser.parse_args().case
    require(
        bool(os.environ.get("E2E_RATE_BACKEND_URL")), "Use e2e_integrations.py --section sellers for isolated quotas"
    )
    harness, foreign = SellerHarness(), SellerHarness()
    override = Path(f".coverage-seller-compose-{harness.run_id}.json")
    settings = {
        "CREDENTIALS_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "RATE_LIMIT_REDIS_URL": os.environ["E2E_RATE_BACKEND_URL"],
        "RATE_LIMIT_REGISTER_LIMIT": "100",
        "RATE_LIMIT_LOGIN_IP_LIMIT": "100",
        "RATE_LIMIT_LOGIN_EMAIL_LIMIT": "100",
        "WEBHOOK_ALLOW_INSECURE_TARGETS": "true",
        "WEBHOOK_EXTRA_ALLOWED_PORTS": "[8080]",
        "WEBHOOK_MAX_ATTEMPTS": "3",
        "WEBHOOK_RETRY_BASE_SECONDS": "1",
        "WEBHOOK_RETRY_MAX_SECONDS": "2",
        "WEBHOOK_DELIVERY_INTERVAL_SECONDS": "1",
        "NOTIFICATIONS_BACKEND": "smtp",
        "SMTP_HOST": "mailpit",
        "SMTP_PORT": "1025",
        "SMTP_USE_TLS": "false",
        "NOTIFICATION_INTERVAL_SECONDS": "1",
        "SSE_MAX_SECONDS": "10",
        "SSE_HEARTBEAT_SECONDS": "0.5",
        "SSE_POLL_SECONDS": "0.05",
        "SSE_LOOKBACK_SECONDS": "0.2",
        "FAILURE_INJECTION_ENABLED": "false",
    }

    def configure(**changes: str) -> None:
        settings.update(changes)
        override.write_text(
            json.dumps({"services": {name: {"environment": settings} for name in ("api", "worker", "scheduler")}}),
            encoding="utf-8",
        )
        compose(
            "-f",
            "docker-compose.yml",
            "-f",
            str(override),
            "--profile",
            "dev-tools",
            "up",
            "-d",
            "--no-build",
            "api",
            "worker",
            "scheduler",
            "mailpit",
            "webhook-sink",
        )
        wait(lambda: healthy(harness), "API ready")
        worker_ready()

    def run(name: str, action: Callable[[], str]) -> None:
        if selected in {"all", name}:
            require(experiment(name, action), "Failed seller experiment " + name)

    try:
        configure()
        harness.initialize()
        foreign.initialize()
        run("E19", harness.shipping)
        run("E22", harness.outbound)
        run("E23", harness.retries)
        if selected in {"all", "E24"}:
            configure(WEBHOOK_ALLOW_INSECURE_TARGETS="false")
            run("E24", harness.ssrf)
            configure(WEBHOOK_ALLOW_INSECURE_TARGETS="true")
        run("E25", harness.notifications)
        if selected in {"all", "E26"}:
            configure(RATE_LIMIT_TRACKING_LIMIT="3")
            run("E26", harness.tracking)
            configure(RATE_LIMIT_TRACKING_LIMIT="60")
        run("E27", lambda: harness.streams(foreign))
        run("E29", lambda: harness.recovery(foreign, configure))
    finally:
        try:
            harness.cleanup()
            foreign.cleanup()
        finally:
            try:
                compose("up", "-d", "--no-build", "api", "worker", "scheduler")
                worker_ready()
            finally:
                override.unlink(missing_ok=True)


def healthy(harness: SellerHarness) -> bool:
    try:
        return harness.client.get("/api/v1/system/health").status_code == 200
    except httpx.TransportError:
        return False


if __name__ == "__main__":
    main()
