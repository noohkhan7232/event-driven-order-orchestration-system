"""Real Docker reliability experiments; leaves uniquely named demo rows for review.

Run after `docker compose up --build -d` with FAILURE_INJECTION_ENABLED=true.
Only this project's worker, relay and RabbitMQ are stopped/restarted; no volumes
or existing data are deleted. Every failed assertion makes the runner exit 1.
"""

import os
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.entities import (  # noqa: E402
    FailedJob,
    Inventory,
    Order,
    OutboxEvent,
    Payment,
    Product,
    Shipment,
    User,
    Warehouse,
)


def experiment(name: str, action: Callable[[], str]) -> bool:
    try:
        details = action()
    except Exception as exc:
        print(f"FAIL {name}: {type(exc).__name__}: {exc}", flush=True)
        return False
    print(f"PASS {name}: {details}", flush=True)
    return True


def compose(*args: str) -> str:
    command = ["docker", "compose", *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)  # noqa: S603
    if result.returncode:
        raise RuntimeError(f"{' '.join(command)}: {result.stdout}\n{result.stderr}")
    return result.stdout.strip()


class Harness:
    def __init__(self) -> None:
        self.run_id = uuid4().hex[:8]
        self.engine = create_engine(
            os.environ.get("E2E_DATABASE_URL", "postgresql+psycopg://orders:orders@localhost:5432/orders")
        )
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        port = os.environ.get("API_PORT", "8000")
        self.client = httpx.Client(
            base_url=os.environ.get("E2E_BASE_URL", f"http://127.0.0.1:{port}"), timeout=20, trust_env=False
        )
        self.headers: dict[str, str] = {}

    def initialize(self) -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if self.client.get("/api/v1/system/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(0.5)
        credentials = {"email": f"e2e-{self.run_id}@example.com", "password": "e2e-demo-password"}
        response = self.client.post(
            "/api/v1/auth/signup", json={**credentials, "full_name": "E2E Reviewer", "tenant_slug": "default"}
        )
        assert response.status_code == 201, response.text
        login = self.client.post("/api/v1/auth/login", json=credentials)
        assert login.status_code == 200, login.text
        self.headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        with self.sessions.begin() as db:
            registered = db.get(User, UUID(response.json()["id"]))
            assert registered is not None and registered.tenant_id is not None
            self.tenant_id = registered.tenant_id
            warehouse = Warehouse(
                tenant_id=self.tenant_id, code=f"E2E-{self.run_id}", name="Reliability proof", region="local-test"
            )
            db.add(warehouse)
            db.flush()
            self.warehouse_id = warehouse.id

    def seed(self, label: str, quantity: int = 10, prefix: str = "") -> str:
        sku = f"{prefix}E2E-{self.run_id}-{label}"
        with self.sessions.begin() as db:
            db.add(Product(tenant_id=self.tenant_id, sku=sku, name="E2E reliability", price="49.99", currency="USD"))
            db.add(
                Inventory(
                    tenant_id=self.tenant_id,
                    sku=sku,
                    product_name="E2E reliability",
                    warehouse_id=self.warehouse_id,
                    available_quantity=quantity,
                )
            )
        return sku

    def create(self, sku: str, key: str | None = None) -> str:
        response = self.client.post(
            "/api/v1/orders",
            headers={**self.headers, "Idempotency-Key": key or uuid4().hex},
            json={"items": [{"sku": sku, "quantity": 1}], "currency": "USD"},
        )
        assert response.status_code == 202, response.text
        return response.json()["id"]

    def poll(self, order_id: str, expected: str, timeout: float = 120) -> dict:
        deadline = time.monotonic() + timeout
        last = {}
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/v1/orders/{order_id}", headers=self.headers)
            response.raise_for_status()
            last = response.json()
            if last["status"] == expected:
                return last
            if last["status"] in {"failed", "cancelled", "delivered"}:
                raise AssertionError(f"Expected {expected}, received {last}")
            time.sleep(0.2)
        raise AssertionError(f"Order timed out waiting for {expected}: {last}")

    def stock(self, sku: str) -> tuple[int, int]:
        with self.sessions() as db:
            row = db.scalar(select(Inventory).where(Inventory.sku == sku))
            assert row is not None
            return row.available_quantity, row.reserved_quantity

    def count(self, model: type, order_ids: list[str], *, initial: bool = False) -> int:
        column = getattr(model, "aggregate_id" if model in {OutboxEvent, FailedJob} else "order_id")
        statement = select(func.count()).select_from(model).where(column.in_([UUID(value) for value in order_ids]))
        if initial:
            statement = statement.where(OutboxEvent.event_type == "order.created")
        with self.sessions() as db:
            return db.scalar(statement) or 0

    def happy(self) -> str:
        sku = self.seed("happy")
        order = self.create(sku)
        self.poll(order, "fulfillment_requested")
        assert self.stock(sku) == (9, 1)
        assert self.count(Payment, [order]) == self.count(Shipment, [order]) == 1
        return "fulfillment_requested; payment=1 shipment=1 stock=(9,1)"

    def business(self, label: str, prefix: str, reason: str, quantity: int = 10) -> str:
        sku = self.seed(label, quantity, prefix)
        order = self.create(sku)
        result = self.poll(order, "failed")
        assert result["failure_reason"] == reason, result
        assert self.stock(sku) == (quantity, 0)
        assert self.count(FailedJob, [order]) == 0
        if prefix == "FAIL-SHIPMENT-":
            assert result["payments"][0]["status"] == "refunded"
        if prefix == "FAIL-PAYMENT-":
            assert result["payments"][0]["status"] == "failed"
        return f"failed/{reason}; stock=({quantity},0); retry jobs=0"

    def crash(self) -> str:
        sku = self.seed("crash")
        compose("kill", "worker")
        try:
            order = self.create(sku)
            time.sleep(1)
        finally:
            compose("start", "worker")
        self.poll(order, "fulfillment_requested")
        assert self.stock(sku) == (9, 1)
        assert self.count(Payment, [order]) == self.count(Shipment, [order]) == 1
        # Explicitly re-deliver the original event through the actual broker.
        code = (
            "from sqlalchemy import select; from app.db.session import SessionLocal; "
            "from app.models.entities import OutboxEvent; from app.events.publisher import EventPublisher; "
            "from app.events.contracts import DomainEvent; from uuid import UUID; "
            f"db=SessionLocal(); row=db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id==UUID('{order}'), "
            "OutboxEvent.event_type=='order.created')); "
            "EventPublisher().publish(DomainEvent.model_validate(row.payload),row.queue); db.close()"
        )
        compose("exec", "-T", "api", "python", "-c", code)
        time.sleep(1)
        assert self.stock(sku) == (9, 1)
        assert self.count(Payment, [order]) == self.count(Shipment, [order]) == 1
        return "SIGKILL before consumption; resumed; broker redelivery; one reservation/payment/shipment"

    def broker_down(self) -> str:
        sku = self.seed("broker")
        compose("stop", "rabbitmq")
        try:
            orders = [self.create(sku) for _ in range(5)]
            with self.sessions() as db:
                pending = db.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_id.in_([UUID(o) for o in orders]),
                        OutboxEvent.event_type == "order.created",
                        OutboxEvent.status == "pending",
                    )
                )
                assert pending == 5, pending
        finally:
            compose("start", "rabbitmq")
        for order in orders:
            self.poll(order, "fulfillment_requested")
        assert self.stock(sku) == (5, 5)
        assert self.count(Payment, orders) == self.count(Shipment, orders) == 5
        return "five HTTP 202; pending backlog=5; recovered all five; no duplicate effects"

    def idempotency(self) -> str:
        sku = self.seed("idempotency")
        key = f"e2e-{self.run_id}"
        first, second = self.create(sku, key), self.create(sku, key)
        assert first == second
        self.poll(first, "fulfillment_requested")
        assert self.count(OutboxEvent, [first], initial=True) == 1
        assert self.count(Payment, [first]) == self.count(Shipment, [first]) == 1
        return "one order; one order.created intent; one payment/shipment"

    def concurrency(self) -> str:
        sku = self.seed("concurrency")
        with ThreadPoolExecutor(max_workers=30) as pool:
            orders = list(pool.map(lambda _: self.create(sku), range(30)))
        deadline = time.monotonic() + 120
        statuses = []
        while time.monotonic() < deadline:
            with self.sessions() as db:
                statuses = list(db.scalars(select(Order.status).where(Order.id.in_([UUID(o) for o in orders]))))
            if all(status.value in {"failed", "fulfillment_requested"} for status in statuses):
                break
            time.sleep(0.3)
        completed = sum(status.value == "fulfillment_requested" for status in statuses)
        failed = sum(status.value == "failed" for status in statuses)
        assert (completed, failed) == (10, 20), (completed, failed)
        assert self.stock(sku) == (0, 10)
        assert self.count(Payment, orders) == self.count(Shipment, orders) == 10
        return "30-vs-10: fulfilled=10 failed=20 available=0 reserved=10 conserved=10"

    def transient(self, exhausted: bool = False) -> str:
        sku = self.seed(
            "transient-exhaust" if exhausted else "transient",
            prefix="FAIL-TRANSIENT-EXHAUST-" if exhausted else "FAIL-TRANSIENT-",
        )
        order = self.create(sku)
        result = self.poll(order, "failed" if exhausted else "fulfillment_requested")
        if exhausted:
            assert result["failure_reason"] == "RETRIES_EXHAUSTED"
            assert self.stock(sku) == (10, 0)
            with self.sessions() as db:
                job = db.scalar(select(FailedJob).where(FailedJob.aggregate_id == UUID(order)))
                assert job and job.payload["event_id"] == str(job.event_id) and job.retry_count == 3
                job_id = job.id
                failed_order = db.get(Order, UUID(order))
                assert failed_order is not None
                actor = failed_order.customer_id
            code = (
                "from app.db.session import SessionLocal; from app.services.failed_jobs import replay_failed_job; "
                "from uuid import UUID; db=SessionLocal(); "
                f"replay_failed_job(db,UUID('{job_id}'),actor=UUID('{actor}')); db.close()"
            )
            os.environ["FAILURE_INJECTION_ENABLED"] = "false"
            try:
                compose("up", "-d", "--no-deps", "--force-recreate", "worker")
                compose("exec", "-T", "api", "python", "-c", code)
                self.poll(order, "fulfillment_requested")
                assert self.stock(sku) == (9, 1)
                assert self.count(Payment, [order]) == self.count(Shipment, [order]) == 1
            finally:
                os.environ["FAILURE_INJECTION_ENABLED"] = "true"
                compose("up", "-d", "--no-deps", "--force-recreate", "worker")
            return "retries exhausted=3; compensated; fresh replay without injection completed; one payment/shipment"
        assert self.stock(sku) == (9, 1)
        assert self.count(Payment, [order]) == self.count(Shipment, [order]) == 1
        return "transient retry succeeds; exactly one reservation/payment/shipment"

    def cancellations(self) -> str:
        compose("stop", "outbox-relay")
        try:
            states = ["created", "inventory_reserved", "paid", "fulfillment_requested"]
            for stage, state in enumerate(states):
                sku = self.seed(f"cancel-{stage}")
                order = self.create(sku)
                for intermediate in states[1 : stage + 1]:
                    code = (
                        "from app.db.session import SessionLocal; from app.workers.outbox_relay import relay_once; "
                        "db=SessionLocal(); relay_once(db,100); db.close()"
                    )
                    compose("exec", "-T", "api", "python", "-c", code)
                    self.poll(order, intermediate)
                for _ in range(2):
                    response = self.client.post(f"/api/v1/orders/{order}/cancel", headers=self.headers)
                    assert response.status_code == 200 and response.json()["status"] == "cancelled", response.text
                assert self.stock(sku) == (10, 0), (state, self.stock(sku))
                result = self.poll(order, "cancelled")
                if stage >= 2:
                    assert result["payments"][0]["status"] == "refunded"
                if stage == 3:
                    assert result["shipments"][0]["status"] == "cancelled"
            return (
                "CREATED/INVENTORY_RESERVED/PAID/FULFILLMENT_REQUESTED; repeated cancel; stock/refund/shipment correct"
            )
        finally:
            compose("start", "outbox-relay")

    def close(self) -> None:
        self.client.close()
        self.engine.dispose()


def main() -> int:
    os.environ["FAILURE_INJECTION_ENABLED"] = "true"
    compose("up", "-d", "--no-build")
    harness = Harness()
    passed: list[bool] = []
    try:
        harness.initialize()
        actions = [
            ("E1 happy path", harness.happy),
            ("E2 insufficient inventory", lambda: harness.business("stock", "", "INSUFFICIENT_INVENTORY", 0)),
            ("E3 payment decline", lambda: harness.business("payment", "FAIL-PAYMENT-", "PAYMENT_DECLINED")),
            ("E4 shipment failure", lambda: harness.business("shipment", "FAIL-SHIPMENT-", "SHIPMENT_FAILED")),
            ("E5 worker crash and redelivery", harness.crash),
            ("E6 broker outage", harness.broker_down),
            ("E7 request idempotency", harness.idempotency),
            ("E8 concurrent orders", harness.concurrency),
            ("S5 transient retry", harness.transient),
            ("S6 exhausted retry and replay", lambda: harness.transient(True)),
            ("S10 cancellation at every requested state", harness.cancellations),
        ]
        for name, action in actions:
            passed.append(experiment(name, action))
    finally:
        compose("start", "rabbitmq", "worker", "outbox-relay")
        harness.close()
    print(f"RESULT: {sum(passed)}/{len(passed)} experiments passed", flush=True)
    print(compose("ps"), flush=True)
    return 0 if all(passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
