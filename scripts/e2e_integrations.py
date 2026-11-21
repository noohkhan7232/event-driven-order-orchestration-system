"""Selected Part1 session experiments against the local mock stack.

Creates unique rows, temporarily disables worker auto-completion, and restores
the base Compose worker configuration in finally. Prints no credentials/bodies.
"""

import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.entities import Payment  # noqa: E402
from scripts.e2e_reliability import Harness, compose  # noqa: E402
from scripts.verify_rate_timeline import body, require  # noqa: E402


def main() -> None:
    harness = Harness()
    override = Path(f".coverage-session-compose-{harness.run_id}.yaml")
    try:
        harness.initialize()
        sku = harness.seed("sessions")
        override.write_text('services:\n  worker:\n    environment:\n      MOCK_PAYMENT_AUTO_COMPLETE: "false"\n')
        compose("-f", "docker-compose.yml", "-f", str(override), "up", "-d", "--no-build", "worker")
        identity = harness.create(sku)
        pending = harness.poll(identity, "payment_pending")
        # PAYMENT_PENDING is committed before provider I/O, so wait for checkout too.
        import time

        deadline = time.monotonic() + 60
        while pending["checkout"] is None:
            require(time.monotonic() < deadline, "Session checkout did not become ready")
            time.sleep(0.2)
            pending = body(harness.client.get(f"/api/v1/orders/{identity}", headers=harness.headers), 200)
        require(
            pending["payment_method"] == "prepaid" and pending["payment_status"] == "pending",
            "Pending summary mismatch",
        )
        require(
            set(pending["checkout"])
            == {"provider", "provider_order_id", "key_id", "amount_minor", "currency", "expires_at"},
            "Checkout field mismatch",
        )
        with harness.sessions() as db:
            rows = list(db.scalars(select(Payment).where(Payment.order_id == UUID(identity))))
            require(len(rows) == 1 and rows[0].idempotency_key == f"{identity}:payment:1", "Attempt identity mismatch")
        cancelled = body(harness.client.post(f"/api/v1/orders/{identity}/cancel", headers=harness.headers), 200)
        require(cancelled["checkout"] is None and cancelled["status"] == "cancelled", "Pending cancellation mismatch")
        require(harness.stock(sku) == (10, 0), "Pending cancellation did not conserve stock")
        print("PASS session subset: pending checkout, durable attempt, cancellation and stock release")
        created = body(
            harness.client.post(
                "/api/v1/orders",
                headers={**harness.headers, "Idempotency-Key": "cod-" + harness.run_id},
                json={"items": [{"sku": sku, "quantity": 1}], "payment_method": "cod"},
            ),
            202,
        )
        cod = harness.poll(created["id"], "fulfillment_requested")
        require(cod["checkout"] is None and cod["payment_status"] == "pending_collection", "COD summary mismatch")
        require(
            len(cod["payments"]) == len(cod["shipments"]) == 1 and cod["payments"][0]["provider"] == "cod",
            "COD effect mismatch",
        )
        body(harness.client.post(f"/api/v1/orders/{created['id']}/cancel", headers=harness.headers), 200)
        require(harness.stock(sku) == (10, 0), "COD cancellation did not conserve stock")
        print("PASS E18 subset: COD fulfillment without session; cancellation releases stock and shipment")
    finally:
        try:
            compose("up", "-d", "--no-build", "worker")
            runtime = compose(
                "exec",
                "-T",
                "worker",
                "python",
                "-c",
                "import json; from app.core.config import get_settings; s=get_settings(); "
                "print(json.dumps({'auto_complete':s.mock_payment_auto_complete,"
                "'allow_mock':s.allow_mock_providers,'failure_injection':s.failure_injection_enabled}))",
            )
            import json

            settings = json.loads(runtime)
            require(
                settings["auto_complete"] and settings["allow_mock"] and not settings["failure_injection"],
                "Worker defaults were not restored",
            )
            print("RESTORED worker runtime " + runtime)
        finally:
            override.unlink(missing_ok=True)
            harness.client.close()
            harness.engine.dispose()


if __name__ == "__main__":
    main()
