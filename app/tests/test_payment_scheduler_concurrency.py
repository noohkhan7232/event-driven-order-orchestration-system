import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from conftest import TestingSessionLocal
from sqlalchemy import select
from test_inbound_payments import webhook as webhook
from test_payment_config import payment_settings as payment_settings
from test_payment_reconciliation import drain_receipts, processing_refund
from test_payment_reconciliation import polling as polling
from test_provider_events import pending_payment as pending_payment

from app.events.contracts import DomainEvent
from app.models.entities import (
    AuditLog,
    FailedJob,
    InboundWebhookEvent,
    Inventory,
    Order,
    OrderTimeline,
    OutboxEvent,
    Payment,
    PaymentReconciliation,
    Refund,
)
from app.models.enums import OrderStatus
from app.providers.contracts import ProviderEvent, ProviderRefund
from app.repositories.orders import OrderRepository
from app.services.inbound_payments import stage_receipt
from app.services.payment_expiry import expire_orders
from app.services.payment_reconciliation import reconcile_payments
from app.services.payment_timing import database_now
from app.services.provider_events import process_provider_event

pytestmark = pytest.mark.postgres


def stage_event(db, tenant_id, notice):
    identity = stage_receipt(db, "razorpay", tenant_id, notice.event_id, [notice])
    db.commit()
    event = DomainEvent.model_validate(
        db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == identity)).payload
    )
    db.commit()
    return event


@pytest.mark.parametrize("capture_first", [True, False])
def test_postgres_expiry_vs_capture_has_one_winner_and_no_double_refund(
    db, pending_payment, monkeypatch, capture_first
):
    from app.services import payment_expiry, provider_events
    from app.services.inventory import InventoryService
    from app.services.refunds import run_refund

    order, _, stock = pending_payment
    deadline = database_now(db)
    order.payment_expires_at = deadline - timedelta(seconds=120)
    db.commit()
    # Two real transactions straddle the same boundary. Order locking decides
    # which observation wins; a cancelled/failed order cannot use early grace.
    monkeypatch.setattr(payment_expiry, "database_now", lambda db: deadline + timedelta(seconds=1))
    monkeypatch.setattr(provider_events, "database_now", lambda db: deadline - timedelta(seconds=1))
    notice = ProviderEvent("evt_race", "payment_captured", "order_provider", "pay_race", None, 1000, "INR", {})
    event = stage_event(db, order.tenant_id, notice)
    locked, release, attempting = Event(), Event(), Event()
    original_get, original_release = OrderRepository.get_with_details, InventoryService.release_for_order

    def capture_lock(repo, *args, **kwargs):
        attempting.set()
        row = original_get(repo, *args, **kwargs)
        if capture_first and kwargs.get("for_update"):
            locked.set()
            assert release.wait(15)
        return row

    def expiry_lock(service, current):
        if not capture_first:
            locked.set()
            assert release.wait(15)
        return original_release(service, current)

    monkeypatch.setattr(OrderRepository, "get_with_details", capture_lock)
    monkeypatch.setattr(InventoryService, "release_for_order", expiry_lock)

    def capture():
        with TestingSessionLocal() as session:
            process_provider_event(session, event)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            capture if capture_first else expire_orders, *(() if capture_first else (TestingSessionLocal,))
        )
        try:
            assert locked.wait(15)
            second = pool.submit(
                expire_orders if capture_first else capture, *((TestingSessionLocal,) if capture_first else ())
            )
            if capture_first:
                assert second.result(timeout=15) == 0
            else:
                assert attempting.wait(15) and not second.done()
        finally:
            release.set()
        first.result(timeout=15)
        second.result(timeout=15)
    db.expire_all()
    assert order.status == (OrderStatus.PAID if capture_first else OrderStatus.FAILED)
    assert (stock.available_quantity, stock.reserved_quantity) == ((9, 1) if capture_first else (10, 0))
    assert db.query(OrderTimeline).filter(OrderTimeline.to_status.in_(["paid", "failed"])).count() == 1
    assert db.query(Refund).count() == (0 if capture_first else 1)
    if not capture_first:
        assert order.failure_reason == "PAYMENT_EXPIRED"
        refund = db.query(Refund).one()
        assert refund.reason == "LATE_PAYMENT"
        duplicate = stage_event(
            db,
            order.tenant_id,
            ProviderEvent(
                "evt_race_duplicate", "payment_captured", "order_provider", "pay_race", None, 1000, "INR", {}
            ),
        )
        with TestingSessionLocal() as session:
            process_provider_event(session, duplicate)
        submitted = []

        class Gateway:
            def refund(self, payment, amount, key):
                submitted.append(key)
                return ProviderRefund("rfnd_race", "processed")

        monkeypatch.setattr("app.services.refunds.resolve_refund_provider", lambda *args: Gateway())
        for _ in range(2):
            run_refund(TestingSessionLocal, refund.id)
        db.expire_all()
        assert submitted == [refund.idempotency_key] and refund.status == "succeeded"
        assert db.query(OutboxEvent).filter_by(event_type="refund.requested").count() == 1
        assert db.query(OutboxEvent).filter_by(event_type="refund.succeeded").count() == 1
    assert db.query(FailedJob).count() == 0
    print(
        "EXPIRY/CAPTURE WINNER="
        + ("CAPTURE" if capture_first else "EXPIRY")
        + "; ONE TRANSITION; STOCK CONSERVED; NO DOUBLE REFUND"
    )


@pytest.mark.parametrize("kind", ["payment", "refund"])
def test_postgres_poll_schedulers_skip_locked_fetch_without_locks_and_dedupe_webhook(
    db, polling, monkeypatch, kind, client, webhook
):
    from app.services import payment_reconciliation

    (order, payment, stock), gateway, _, results = polling
    refund = processing_refund(db, order) if kind == "refund" else None
    if refund:
        refund.provider_refund_id = "rfnd_concurrent"
        db.commit()
    baseline_receipts = db.query(InboundWebhookEvent).count()
    db.commit()
    claim_entered, claim_release, fetching, fetch_release = Event(), Event(), Event(), Event()
    calls = []

    def resolve(*args):
        claim_entered.set()
        assert claim_release.wait(15)
        return gateway

    def fetch(reference):
        calls.append(reference)
        fetching.set()
        assert fetch_release.wait(15)
        return results if kind == "payment" else ProviderRefund(reference, "processed")

    monkeypatch.setattr(payment_reconciliation, "resolve_refund_provider", resolve)
    monkeypatch.setattr(gateway, "fetch_payment_status" if kind == "payment" else "fetch_refund", fetch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reconcile_payments, TestingSessionLocal)
        try:
            assert claim_entered.wait(15)
            # Lease is still uncommitted: only actual SKIP LOCKED can avoid work.
            assert reconcile_payments(TestingSessionLocal) == {"payments": 0, "refunds": 0, "errors": 0}
            claim_release.set()
            assert fetching.wait(15)
            with TestingSessionLocal() as observer:
                for _model, column, identity in (
                    (Order, Order.id, order.id),
                    (Inventory, Inventory.id, stock.id),
                    (Payment, Payment.id, payment.id),
                    (Refund, Refund.id, refund.id)
                    if refund
                    else (PaymentReconciliation, PaymentReconciliation.payment_id, payment.id),
                ):
                    assert (
                        observer.scalar(select(column).where(column == identity).with_for_update(nowait=True))
                        == identity
                    )
            entity = {"amount": 1000, "currency": "INR"}
            entity.update(
                {"id": "pay_polled", "order_id": "order_provider"}
                if kind == "payment"
                else {"id": "rfnd_concurrent", "payment_id": "pay_provider", "status": "processed"}
            )
            event_id = "evt_actual_" + kind
            raw = json.dumps(
                {
                    "event": "payment.captured" if kind == "payment" else "refund.processed",
                    "payload": {kind: {"entity": entity}},
                }
            ).encode()
            headers = {
                "X-Razorpay-Signature": hmac.new(b"synthetic-hook", raw, hashlib.sha256).hexdigest(),
                "X-Razorpay-Event-Id": event_id,
            }
            assert webhook[1] == order.tenant_id
            for _ in range(2):
                response = client.post(webhook[0], content=raw, headers=headers)
                assert response.status_code == 200 and response.json() == {"accepted": True}
            with TestingSessionLocal() as session:
                receipt = session.scalar(
                    select(InboundWebhookEvent).where(
                        InboundWebhookEvent.tenant_id == order.tenant_id,
                        InboundWebhookEvent.provider_event_id == event_id,
                    )
                )
                event = DomainEvent.model_validate(
                    session.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == receipt.id)).payload
                )
                session.commit()
                process_provider_event(session, event)
            db.expire_all()
            assert (refund.status == "succeeded") if refund else (order.status == OrderStatus.PAID)
        finally:
            claim_release.set()
            fetch_release.set()
        counts = future.result(timeout=15)
    assert counts == {"payments": int(kind == "payment"), "refunds": int(kind == "refund"), "errors": 0}
    db.expire_all()
    drain_receipts(db)
    assert len(calls) == 1 and db.query(InboundWebhookEvent).count() == baseline_receipts + 2
    assert db.query(FailedJob).count() == 0
    if refund:
        assert refund.status == "succeeded"
        assert db.query(OutboxEvent).filter_by(event_type="refund.succeeded").count() == 1
        assert db.query(AuditLog).filter_by(action="payment_refunded").count() == 1
    else:
        assert order.status == OrderStatus.PAID and db.query(Payment).count() == 1
        assert db.query(OrderTimeline).filter_by(to_status="paid").count() == 1
    assert stock.available_quantity + stock.reserved_quantity == 10
    print(
        "POLL "
        + kind.upper()
        + ": PEER SKIP LOCKED; ORDER/INVENTORY/PAYMENT/CLAIM NOWAIT DURING FETCH; REAL WEBHOOK DEDUPED"
    )


def test_postgres_expiry_during_poll_keeps_late_financial_truth_and_refund(db, polling, monkeypatch):
    (order, _, stock), gateway, _, results = polling
    order.payment_expires_at = database_now(db) - timedelta(seconds=121)
    db.commit()
    entered, release = Event(), Event()

    def fetch(reference):
        entered.set()
        assert release.wait(15)
        return results

    monkeypatch.setattr(gateway, "fetch_payment_status", fetch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reconcile_payments, TestingSessionLocal)
        try:
            assert entered.wait(15)
            assert expire_orders(TestingSessionLocal) == 1
        finally:
            release.set()
        assert future.result(timeout=15)["payments"] == 1
    db.expire_all()
    drain_receipts(db)
    drain_receipts(db)
    assert order.status == OrderStatus.FAILED and order.failure_reason == "PAYMENT_EXPIRED"
    assert (stock.available_quantity, stock.reserved_quantity) == (10, 0)
    assert db.query(Refund).one().reason == "LATE_PAYMENT"
    assert db.query(OutboxEvent).filter_by(event_type="refund.requested").count() == 1
    print("EXPIRY DURING PROVIDER POLL: LATE TRUTH JOURNALED; ONE REFUND; STOCK RESTORED")
