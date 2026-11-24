"""Full Phase 3B Docker acceptance, with targeted --section reruns.

    Uses unique fixture data and isolated Redis quotas; restores base runtime values
and preserves volumes. Razorpay HTTP ingestion uses dummy session fixtures only.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.entities import Payment  # noqa: E402
from scripts.e2e_reliability import Harness, compose, worker_ready  # noqa: E402
from scripts.verify_rate_timeline import body, require  # noqa: E402


def session_cod() -> None:
    harness = Harness()
    override = Path(f".coverage-session-compose-{harness.run_id}.yaml")
    try:
        harness.initialize()
        sku = harness.seed("sessions")
        override.write_text('services:\n  worker:\n    environment:\n      MOCK_PAYMENT_AUTO_COMPLETE: "false"\n')
        compose("-f", "docker-compose.yml", "-f", str(override), "up", "-d", "--no-build", "worker")
        worker_ready()
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
        print("PASS E18: COD fulfillment without session; cancellation releases stock and shipment")
    finally:
        try:
            compose("up", "-d", "--no-build", "worker")
            worker_ready()
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3B Docker acceptance; preserves existing volumes/data")
    parser.add_argument(
        "--section", choices=("all", "legacy", "payments", "abuse", "sessions", "sellers", "hardening"), default="all"
    )
    parser.add_argument(
        "--seller-case",
        choices=("all", "hardening", "E19", "E22", "E23", "E24", "E25", "E26", "E27", "E29"),
        default="all",
    )
    arguments = parser.parse_args()
    section = arguments.section
    root = Path(__file__).resolve().parents[1]
    override = root / (".coverage-part1-compose-" + uuid4().hex + ".yaml")
    names = ("COMPOSE_FILE", "COMPOSE_PATH_SEPARATOR", "FAILURE_INJECTION_ENABLED", "E2E_RATE_BACKEND_URL")
    original = {name: os.environ.get(name) for name in names}
    # Fresh, volume-free Redis keeps every HTTP quota real and repeatable without
    # consuming or deleting a seller's normal shared abuse windows.
    rate_service = "proof-rate-" + uuid4().hex[:10]
    rate_url = "redis://" + rate_service + ":6379/0"
    override.write_text(
        json.dumps(
            {
                "services": {
                    rate_service: {
                        "image": "redis:7-alpine",
                        "tmpfs": ["/data"],
                        "healthcheck": {
                            "test": ["CMD", "redis-cli", "ping"],
                            "interval": "1s",
                            "timeout": "2s",
                            "retries": 30,
                        },
                    },
                    "api": {
                        "environment": {"RATE_LIMIT_REDIS_URL": rate_url},
                        "depends_on": {rate_service: {"condition": "service_healthy"}},
                    },
                }
            }
        )
    )
    os.environ["COMPOSE_PATH_SEPARATOR"] = os.pathsep
    os.environ["COMPOSE_FILE"] = os.pathsep.join((str(root / "docker-compose.yml"), str(override)))
    os.environ["E2E_RATE_BACKEND_URL"] = rate_url
    commands = []
    if section in {"all", "legacy"}:
        commands.extend(("e2e_reliability.py", "e2e_tenancy.py"))
    if section == "hardening":
        commands.append("e2e_reliability.py")
    if section in {"all", "payments"}:
        commands.append("e2e_payments.py")
    if section in {"all", "abuse"}:
        commands.append("e2e_abuse_inventory.py")
    failures = []
    try:
        compose("config", "--quiet")
        compose("up", "-d", "--no-build", "--wait", rate_service)
        print("ISOLATED Redis HTTP quotas; normal quota counters and all data volumes preserved", flush=True)
        for filename in commands:
            arguments_for_script = [sys.executable, str(root / "scripts" / filename)]
            if section == "hardening":
                arguments_for_script.append("--smoke")
            result = subprocess.run(arguments_for_script, check=False)  # noqa: S603
            if result.returncode:
                failures.append(filename)
        if section in {"all", "sessions"}:
            session_cod()
        if section in {"all", "sellers", "hardening"}:
            result = subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    str(root / "scripts/e2e_seller_integrations.py"),
                    "--case",
                    "hardening" if section == "hardening" else arguments.seller_case,
                ],
                check=False,
            )
            if result.returncode:
                failures.append("e2e_seller_integrations.py")
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        try:
            os.environ["FAILURE_INJECTION_ENABLED"] = "false"
            compose("up", "-d", "--no-build")
            worker_ready()
            worker_ready(service="integrations-worker")
            for service in ("api", "worker", "scheduler", "integrations-worker"):
                runtime = compose(
                    "exec",
                    "-T",
                    service,
                    "python",
                    "-c",
                    "import json; from app.core.config import get_settings; s=get_settings(); "
                    "print(json.dumps({'auto_complete':s.mock_payment_auto_complete,"
                    "'refund_failures':s.mock_refund_fail_attempts,'window':s.payment_window_seconds,"
                    "'grace':s.payment_capture_grace_seconds,'expiry_interval':s.payment_expiry_interval_seconds,"
                    "'reconcile_after':s.payment_reconcile_after_seconds,"
                    "'reconcile_interval':s.payment_reconcile_interval_seconds,"
                    "'failure_injection':s.failure_injection_enabled,"
                    "'shared_rate_backend':bool(s.rate_limit_redis_url),"
                    "'rate_backend_is_default':s.rate_limit_redis_url=='redis://redis:6379/2',"
                    "'encryption_configured':s.credentials_encryption_key is not None,"
                    "'notifications_backend':s.notifications_backend,"
                    "'webhook_insecure':s.webhook_allow_insecure_targets,"
                    "'webhook_attempts':s.webhook_max_attempts,'webhook_interval':s.webhook_delivery_interval_seconds,"
                    "'webhook_total_timeout':s.webhook_total_timeout_seconds,"
                    "'webhook_dns_timeout':s.webhook_dns_timeout_seconds,"
                    "'webhook_batch_budget':s.webhook_batch_budget_seconds,"
                    "'notification_interval':s.notification_interval_seconds,"
                    "'stream_max':s.sse_max_seconds,'stream_poll':s.sse_poll_seconds,"
                    "'stream_lookback':s.sse_lookback_seconds,'tracking_limit':s.rate_limit_tracking_limit}))",
                )
                values = json.loads(runtime)
                require(
                    values
                    == {
                        "auto_complete": True,
                        "refund_failures": 0,
                        "window": 1800,
                        "grace": 120,
                        "expiry_interval": 30,
                        "reconcile_after": 60,
                        "reconcile_interval": 60,
                        "failure_injection": False,
                        "shared_rate_backend": True,
                        "rate_backend_is_default": True,
                        "encryption_configured": False,
                        "notifications_backend": "console",
                        "webhook_insecure": False,
                        "webhook_attempts": 8,
                        "webhook_interval": 10,
                        "webhook_total_timeout": 15,
                        "webhook_dns_timeout": 3,
                        "webhook_batch_budget": 45,
                        "notification_interval": 10,
                        "stream_max": 300,
                        "stream_poll": 1,
                        "stream_lookback": 5,
                        "tracking_limit": 60,
                    },
                    "Phase 3B defaults not restored",
                )
                print("RESTORED " + service + " " + runtime, flush=True)
        finally:
            if original["FAILURE_INJECTION_ENABLED"] is None:
                os.environ.pop("FAILURE_INJECTION_ENABLED", None)
            else:
                os.environ["FAILURE_INJECTION_ENABLED"] = original["FAILURE_INJECTION_ENABLED"]
            try:
                compose(
                    "-f", str(root / "docker-compose.yml"), "-f", str(override), "rm", "-f", "-s", "-v", rate_service
                )
            finally:
                override.unlink(missing_ok=True)
        restored = compose("ps", "-a")
        require(rate_service not in restored, "Temporary quota service was not removed")
        print(restored, flush=True)
    require(not failures, "Failed acceptance scripts: " + ",".join(failures))
    print("PASS selected Phase 3B acceptance section: " + section, flush=True)


if __name__ == "__main__":
    main()
