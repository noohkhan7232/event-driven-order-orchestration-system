"""Part 1 Docker payment proofs; no external payment HTTP or real credentials.

Creates unique demo rows. The HTTP capture proof explicitly relabels a pending
mock session as a Razorpay fixture; it does not create a remote Razorpay order.
Temporary encryption keys/config and worker overrides are removed in finally.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.entities import (  # noqa: E402
    AuditLog,
    InboundWebhookEvent,
    OutboxEvent,
    Payment,
    Refund,
    Shipment,
    ShipmentEvent,
    TenantPaymentConfig,
)
from app.models.enums import PaymentStatus, ShipmentStatus  # noqa: E402
from scripts.e2e_reliability import Harness, compose, worker_ready  # noqa: E402
from scripts.verify_rate_timeline import body, require  # noqa: E402


def wait(check, label):
    deadline = time.monotonic() + 120
    while True:
        result = check()
        if result:
            return result
        require(time.monotonic() < deadline, "Timed out: " + label)
        time.sleep(0.25)


def refund_done(harness, order_id, reason):
    def check():
        with harness.sessions() as db:
            row = db.scalar(select(Refund).where(Refund.order_id == UUID(order_id), Refund.reason == reason))
            return row if row and row.status == "succeeded" else None

    return wait(check, "refund " + reason)


def signature(payload, secret, identity):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return raw, {
        "X-Razorpay-Event-Id": identity,
        "X-Razorpay-Signature": hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
    }


def capture(order_ref, amount, currency, payment_id):
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_ref,
                    "amount": amount,
                    "currency": currency,
                }
            }
        },
    }


def stage_mock(harness, checkout, amount, payment_id):
    # Sign/verify inside the real worker with its configured simulator secret.
    # Only non-secret financial fixture identifiers cross the CLI boundary.
    encoded = base64.b64encode(
        json.dumps(capture(checkout["provider_order_id"], amount, checkout["currency"], payment_id)).encode()
    ).decode()
    code = (
        "import base64,hmac; from uuid import UUID; from app.core.config import get_settings; "
        "from app.providers.mock_v2 import MockPaymentProviderV2; from app.db.session import SessionLocal; "
        "from app.services.inbound_payments import stage_receipt; s=get_settings(); "
        f"raw=base64.b64decode('{encoded}'); identity='evt_{uuid4().hex}'; "
        "headers={'X-Razorpay-Event-Id':identity,'X-Razorpay-Signature':"
        "hmac.new(s.mock_webhook_secret.get_secret_value().encode(),raw,'sha256').hexdigest()}; "
        "notices=MockPaymentProviderV2(s).verify_and_parse_webhook(headers,raw,s.mock_webhook_secret); "
        f"db=SessionLocal(); stage_receipt(db,'mock',UUID('{harness.tenant_id}'),identity,notices); "
        "db.commit(); db.close()"
    )
    compose("exec", "-T", "worker", "python", "-c", code)


def reliability(harness):
    # Stop the real shipment consumer so PAID is observable without assigning
    # financial state or racing an automatically created shipment.
    def shipment_consumer(action):
        compose(
            "exec",
            "-T",
            "worker",
            "celery",
            "-A",
            "app.workers.celery_app",
            "control",
            action,
            "shipments",
            "--timeout=15",
        )

    shipment_consumer("cancel_consumer")
    try:
        sku = harness.seed("paid-refund-retry")
        identity = harness.create(sku)
        harness.poll(identity, "paid")
        for _ in range(2):
            body(harness.client.post(f"/api/v1/orders/{identity}/cancel", headers=harness.headers), 200)
        refund = refund_done(harness, identity, "ORDER_CANCELLED")
        with harness.sessions() as db:
            payment = db.get(Payment, refund.payment_id)
            require(refund.attempts == 3 and payment.status == PaymentStatus.REFUNDED, "PAID refund retry mismatch")
            require(
                db.scalar(select(func.count()).select_from(Shipment).where(Shipment.order_id == UUID(identity))) == 0,
                "PAID cancellation created a shipment",
            )
            require(
                db.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        AuditLog.entity_id == payment.id,
                        AuditLog.action == "payment_refunded",
                    )
                )
                == 1,
                "PAID cancellation duplicated the refund audit",
            )
            require(
                db.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_id == UUID(identity),
                        OutboxEvent.event_type == "refund.succeeded",
                    )
                )
                == 1,
                "PAID cancellation duplicated the success intent",
            )
        require(harness.stock(sku) == (10, 0), "PAID cancellation stock mismatch")
        print(
            "PASS E20 PAID: real shipment consumer paused; attempts=3; one refund/audit/intent; stock restored",
            flush=True,
        )
    finally:
        shipment_consumer("add_consumer")
    fulfilled_refund_reliability(harness)


def fulfilled_refund_reliability(harness):
    sku = harness.seed("refund-retry")
    identity = harness.create(sku)
    harness.poll(identity, "fulfillment_requested")
    for _ in range(2):
        body(harness.client.post(f"/api/v1/orders/{identity}/cancel", headers=harness.headers), 200)
    refund = refund_done(harness, identity, "ORDER_CANCELLED")
    with harness.sessions() as db:
        payment = db.get(Payment, refund.payment_id)
        require(refund.attempts == 3 and payment.status == PaymentStatus.REFUNDED, "Refund retry/effect mismatch")
        require(
            db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == payment.id, AuditLog.action == "payment_refunded")
            )
            == 1,
            "Duplicate financial audit",
        )
        require(
            db.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.aggregate_id == UUID(identity), OutboxEvent.event_type == "refund.succeeded")
            )
            == 1,
            "Duplicate success intent",
        )
        shipment = db.scalar(select(Shipment).where(Shipment.order_id == UUID(identity)))
        require(shipment.status == ShipmentStatus.CANCELLED, "Shipment not cancelled")
        require(
            db.scalar(
                select(func.count())
                .select_from(ShipmentEvent)
                .where(ShipmentEvent.shipment_id == shipment.id, ShipmentEvent.status == ShipmentStatus.CANCELLED)
            )
            == 1,
            "Duplicate shipment cancellation",
        )
    require(harness.stock(sku) == (10, 0), "Refund cancellation stock mismatch")
    print(
        "PASS E20: broker/prefork worker; two transient failures, attempts=3, one refund success/audit; "
        "shipment cancelled, stock restored",
        flush=True,
    )


def mismatch_and_late(harness):
    sku = harness.seed("mismatch-late")
    identity = harness.create(sku)
    checkout = wait(
        lambda: body(harness.client.get(f"/api/v1/orders/{identity}", headers=harness.headers), 200)["checkout"],
        "mock checkout",
    )
    stage_mock(harness, checkout, checkout["amount_minor"] + 100, "mock_wrong_" + harness.run_id)
    refund_done(harness, identity, "AMOUNT_MISMATCH")
    pending = body(harness.client.get(f"/api/v1/orders/{identity}", headers=harness.headers), 200)
    require(
        pending["status"] == "payment_pending" and pending["payment_status"] == "pending" and not pending["shipments"],
        "Mismatch was fulfilled or replaced primary summary",
    )
    print(
        "PASS E17 pipeline: signed mock mismatch -> receipt -> broker consumer -> async refund; pending, no shipment",
        flush=True,
    )
    body(harness.client.post(f"/api/v1/orders/{identity}/cancel", headers=harness.headers), 200)
    stage_mock(harness, checkout, checkout["amount_minor"], "mock_late_" + harness.run_id)
    refund_done(harness, identity, "LATE_PAYMENT")
    cancelled = body(harness.client.get(f"/api/v1/orders/{identity}", headers=harness.headers), 200)
    require(
        cancelled["status"] == "cancelled" and not cancelled["shipments"] and harness.stock(sku) == (10, 0),
        "Late payment changed cancellation",
    )
    print(
        "PASS late-cancel pipeline: signed late payment refunded, cancellation and stock preserved "
        "(beat expiry is checked separately by E16)",
        flush=True,
    )


def http_capture():
    harness = Harness()
    config_id = None
    try:
        email, password = f"webhook-{harness.run_id}@example.com", secrets.token_urlsafe(24)
        registered = body(
            harness.client.post(
                "/api/v1/tenants/register",
                json={
                    "business_name": "Webhook proof",
                    "slug": "hooks-" + harness.run_id,
                    "owner_email": email,
                    "owner_password": password,
                    "owner_name": "Proof owner",
                },
            ),
            201,
        )
        harness.tenant_id = UUID(registered["id"])
        login = body(harness.client.post("/api/v1/auth/login", json={"email": email, "password": password}), 200)
        harness.headers = {"Authorization": "Bearer " + login["access_token"]}
        sku = "HOOK-" + harness.run_id
        body(
            harness.client.post(
                "/api/v1/products",
                headers=harness.headers,
                json={"sku": sku, "name": "Webhook fixture", "price": "1.00", "currency": "INR"},
            ),
            201,
        )
        warehouse = body(
            harness.client.post(
                "/api/v1/inventory/warehouses",
                headers=harness.headers,
                json={"code": "MAIN", "name": "Proof", "region": "test"},
            ),
            201,
        )
        body(
            harness.client.post(
                "/api/v1/inventory",
                headers=harness.headers,
                json={"sku": sku, "warehouse_id": warehouse["id"], "available_quantity": 10},
            ),
            201,
        )
        created = body(
            harness.client.post(
                "/api/v1/orders",
                headers={**harness.headers, "Idempotency-Key": "hooks-" + harness.run_id},
                json={"items": [{"sku": sku, "quantity": 1}]},
            ),
            202,
        )
        identity = created["id"]
        checkout = wait(
            lambda: body(harness.client.get(f"/api/v1/orders/{identity}", headers=harness.headers), 200)["checkout"],
            "HTTP proof checkout",
        )
        secret = secrets.token_urlsafe(32)
        config = body(
            harness.client.put(
                "/api/v1/settings/payments/razorpay",
                headers=harness.headers,
                json={
                    "key_id": "fixture_public",
                    "key_secret": secrets.token_urlsafe(32),
                    "webhook_secret": secret,
                    "mode": "test",
                },
            ),
            200,
        )
        with harness.sessions.begin() as db:
            stored = db.scalar(select(TenantPaymentConfig).where(TenantPaymentConfig.tenant_id == harness.tenant_id))
            config_id = stored.id
            # Explicit local fixture only: no Razorpay session/order API is called.
            payment = db.scalar(select(Payment).where(Payment.order_id == UUID(identity)))
            payment.provider = "razorpay"
        path = "/api/v1/webhooks/payments/razorpay/" + config["webhook_public_id"]
        raw, headers = signature(
            capture(checkout["provider_order_id"], 100, "INR", "pay_fixture_" + harness.run_id),
            secret,
            "evt_fixture_" + harness.run_id,
        )
        require(harness.client.post(path, content=raw).status_code == 401, "Missing signature accepted")
        require(
            harness.client.post(path, content=raw + b" ", headers=headers).status_code == 401,
            "Changed raw bytes accepted",
        )
        require(
            harness.client.post(path, content=b"x" * 65537, headers=headers).status_code == 413,
            "Oversized body accepted",
        )
        with harness.sessions() as db:
            require(
                db.scalar(
                    select(func.count())
                    .select_from(InboundWebhookEvent)
                    .where(InboundWebhookEvent.tenant_id == harness.tenant_id)
                )
                == 0,
                "Invalid request stored",
            )
        for _ in range(2):
            body(harness.client.post(path, content=raw, headers=headers), 200)
        result = harness.poll(identity, "fulfillment_requested")
        require(len(result["payments"]) == len(result["shipments"]) == 1, "Duplicate payment/shipment")
        with harness.sessions() as db:
            receipts = list(
                db.scalars(select(InboundWebhookEvent).where(InboundWebhookEvent.tenant_id == harness.tenant_id))
            )
            require(len(receipts) == 1 and receipts[0].status == "processed", "Duplicate receipt effect")
            require(
                db.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_id == receipts[0].id, OutboxEvent.event_type == "payment.provider_event"
                    )
                )
                == 1,
                "Duplicate receipt intent",
            )
        print(
            "PASS E15 HTTP fixture: raw HMAC accepted once, duplicate=200, bad/missing=401, oversize=413; "
            "broker fulfillment one payment/shipment; NO remote Razorpay session/money",
            flush=True,
        )
    finally:
        if config_id:
            with harness.sessions.begin() as db:
                row = db.get(TenantPaymentConfig, config_id)
                if row:
                    db.delete(row)
        harness.client.close()
        harness.engine.dispose()


def expiry_and_late(harness):
    sku = harness.seed("expiry-late")
    identity = harness.create(sku)
    checkout = wait(
        lambda: body(harness.client.get(f"/api/v1/orders/{identity}", headers=harness.headers), 200)["checkout"],
        "expiry checkout",
    )
    expired = harness.poll(identity, "failed")
    require(
        expired["failure_reason"] == "PAYMENT_EXPIRED" and harness.stock(sku) == (10, 0),
        "Beat expiry did not release stock",
    )
    payment_id = "mock_expired_" + harness.run_id
    for _ in range(2):
        stage_mock(harness, checkout, checkout["amount_minor"], payment_id)
    refund_done(harness, identity, "LATE_PAYMENT")
    result = body(harness.client.get(f"/api/v1/orders/{identity}", headers=harness.headers), 200)
    require(
        result["status"] == "failed" and not result["shipments"] and harness.stock(sku) == (10, 0),
        "Late payment changed expired order",
    )
    with harness.sessions() as db:
        require(
            db.scalar(select(func.count()).select_from(Refund).where(Refund.order_id == UUID(identity))) == 1,
            "Duplicate late refund",
        )
        require(
            db.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.aggregate_id == UUID(identity), OutboxEvent.event_type == "refund.succeeded")
            )
            == 1,
            "Duplicate late refund success",
        )
    print(
        "PASS E16: actual beat expiry after window+grace, stock restored; "
        "signed late capture twice, one succeeded refund, no fulfillment",
        flush=True,
    )


def main():
    harness = Harness()
    override = Path(f".coverage-refund-compose-{harness.run_id}.yaml")
    key = Fernet.generate_key().decode()

    def configure(auto, failures, window=1800, grace=120, interval=30):
        timing = {
            "PAYMENT_WINDOW_SECONDS": str(window),
            "PAYMENT_CAPTURE_GRACE_SECONDS": str(grace),
            "PAYMENT_EXPIRY_INTERVAL_SECONDS": str(interval),
            "PAYMENT_RECONCILE_AFTER_SECONDS": "3600",
        }
        override.write_text(
            json.dumps(
                {
                    "services": {
                        "api": {
                            "environment": {
                                "CREDENTIALS_ENCRYPTION_KEY": key,
                                "RATE_LIMIT_REDIS_URL": os.environ.get("E2E_RATE_BACKEND_URL", ""),
                                **timing,
                            }
                        },
                        "worker": {
                            "environment": {
                                "MOCK_PAYMENT_AUTO_COMPLETE": str(auto).lower(),
                                "MOCK_REFUND_FAIL_ATTEMPTS": str(failures),
                                **timing,
                            }
                        },
                        "scheduler": {"environment": timing},
                    }
                }
            )
        )
        compose("-f", "docker-compose.yml", "-f", str(override), "up", "-d", "--no-build", "api", "worker", "scheduler")

        def ready():
            try:
                return harness.client.get("/api/v1/system/health").status_code == 200
            except httpx.TransportError:
                return False

        wait(ready, "API ready")
        worker_ready()

    try:
        configure(True, 2)
        harness.initialize()
        reliability(harness)
        configure(False, 0)
        mismatch_and_late(harness)
        http_capture()
        configure(False, 0, window=3, grace=2, interval=1)
        expiry_and_late(harness)
    finally:
        try:
            compose("up", "-d", "--no-build", "api", "worker", "scheduler")
            worker_ready()
            for service in ("api", "worker"):
                runtime = compose(
                    "exec",
                    "-T",
                    service,
                    "python",
                    "-c",
                    "import json; from app.core.config import get_settings; s=get_settings(); "
                    "print(json.dumps({'auto_complete':s.mock_payment_auto_complete,"
                    "'refund_fail_attempts':s.mock_refund_fail_attempts,'allow_mock':s.allow_mock_providers,"
                    "'failure_injection':s.failure_injection_enabled,"
                    "'encryption_configured':s.credentials_encryption_key is not None}))",
                )
                settings = json.loads(runtime)
                require(
                    settings
                    == {
                        "auto_complete": True,
                        "refund_fail_attempts": 0,
                        "allow_mock": True,
                        "failure_injection": False,
                        "encryption_configured": False,
                    },
                    "Runtime defaults not restored",
                )
                print("RESTORED " + service + " " + runtime, flush=True)
        finally:
            override.unlink(missing_ok=True)
            harness.client.close()
            harness.engine.dispose()


if __name__ == "__main__":
    main()
