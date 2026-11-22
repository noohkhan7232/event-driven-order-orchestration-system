from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_provider_events import deliver
from test_provider_events import pending_payment as pending_payment

from app.models.entities import FailedJob, InboundWebhookEvent, OrderTimeline, OutboxEvent, Payment, Refund
from app.models.enums import OrderStatus, PaymentStatus
from app.providers.contracts import ProviderEvent, ProviderRefund, ProviderUnavailable
from app.workers import tasks


@pytest.fixture
def polling(db, pending_payment, monkeypatch):
    from app.core.config import get_settings
    from app.services import payment_reconciliation

    monkeypatch.setattr(get_settings(), "payment_reconcile_after_seconds", 0)
    calls = []
    results = [
        ProviderEvent(
            "external_unstable",
            "payment_captured",
            "order_provider",
            "pay_polled",
            None,
            1000,
            "INR",
            {"secret": "synthetic-private-provider-data"},
        )
    ]

    class Gateway:
        def fetch_payment_status(self, reference):
            assert not db.in_transaction(), "Provider fetch must run outside a transaction"
            calls.append(("payment", reference))
            return results

        def fetch_refund(self, reference):
            assert not db.in_transaction(), "Refund fetch must run outside a transaction"
            calls.append(("refund", reference))
            return ProviderRefund(reference, "processed")

        def find_refund(self, payment, amount, key):
            assert not db.in_transaction(), "Refund lookup must run outside a transaction"
            calls.append(("find", key))
            return None

        def refund(self, payment, amount, key):
            calls.append(("submit", key))
            return ProviderRefund("rfnd_reissued", "processed")

    gateway = Gateway()
    monkeypatch.setattr(payment_reconciliation, "resolve_refund_provider", lambda *args: gateway)
    return pending_payment, gateway, calls, results


def drain_receipts(db):
    receipts = db.scalars(select(InboundWebhookEvent).where(InboundWebhookEvent.status == "received")).all()
    for receipt in receipts:
        intent = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == receipt.id))
        tasks.process_event.run(intent.payload)


def test_payment_poll_uses_same_journal_and_deterministic_id_without_duplicate_money(db, polling):
    (order, payment, stock), _, calls, results = polling
    for _ in range(2):
        assert tasks.reconcile_pending_payments.run() == {"payments": 1, "refunds": 0, "errors": 0}
    assert len(calls) == 2
    receipt = db.query(InboundWebhookEvent).one()
    assert receipt.provider_event_id.startswith("reconcile_") and "synthetic-private" not in repr(receipt.payload)
    assert db.query(OutboxEvent).filter_by(event_type="payment.provider_event").count() == 1
    # A real webhook has its own provider event ID but the same captured payment.
    deliver(db, order, event_id="evt_real", payment_id=results[0].provider_payment_id)
    drain_receipts(db)
    assert order.status == OrderStatus.PAID and payment.status == PaymentStatus.AUTHORIZED
    assert db.query(Payment).count() == 1 and db.query(Refund).count() == 0
    assert db.query(OrderTimeline).filter_by(to_status="paid").count() == 1
    assert (stock.available_quantity, stock.reserved_quantity) == (9, 1)
    assert tasks.reconcile_pending_payments.run()["payments"] == 0


def test_reconciliation_respects_order_age_and_durable_poll_checkpoint(db, polling, monkeypatch):
    from app.core.config import get_settings
    from app.models.entities import PaymentReconciliation
    from app.services.payment_timing import database_now

    (order, payment, _), _, calls, results = polling
    results.clear()
    monkeypatch.setattr(get_settings(), "payment_reconcile_after_seconds", 60)
    assert tasks.reconcile_pending_payments.run()["payments"] == 0
    order.created_at = database_now(db) - timedelta(minutes=10)
    db.commit()
    assert tasks.reconcile_pending_payments.run()["payments"] == 1
    assert tasks.reconcile_pending_payments.run()["payments"] == 0
    checkpoint = db.get(PaymentReconciliation, payment.id)
    checkpoint.last_checked_at = database_now(db) - timedelta(seconds=61)
    db.commit()
    assert tasks.reconcile_pending_payments.run()["payments"] == 1 and len(calls) == 2


@pytest.mark.parametrize("outcome", ["unavailable", "foreign", "malformed"])
def test_bad_poll_outcomes_never_change_money_or_leak_provider_text(db, polling, monkeypatch, caplog, outcome):
    from app.models.entities import PaymentReconciliation

    (order, payment, _), gateway, _, results = polling
    if outcome == "unavailable":

        def broken(reference):
            raise ProviderUnavailable("synthetic-private-provider-data")

        monkeypatch.setattr(gateway, "fetch_payment_status", broken)
    else:
        from dataclasses import replace

        results[:] = [
            replace(results[0], provider_order_id="order_foreign")
            if outcome == "foreign"
            else replace(results[0], amount_minor=True)
        ]
    assert tasks.reconcile_pending_payments.run() == {"payments": 1, "refunds": 0, "errors": 1}
    assert order.status == OrderStatus.PAYMENT_PENDING and db.query(InboundWebhookEvent).count() == 0
    checkpoint = db.get(PaymentReconciliation, payment.id)
    assert checkpoint.claim_token is None and checkpoint.lease_until is None
    assert checkpoint.last_error == "provider_unavailable"
    assert "synthetic-private-provider-data" not in caplog.text + checkpoint.last_error
    assert db.query(FailedJob).count() == 0


def test_missing_provider_configuration_is_closed_without_failing_the_order(db, pending_payment, monkeypatch):
    from app.core.config import get_settings

    order, _, _ = pending_payment
    monkeypatch.setattr(get_settings(), "payment_reconcile_after_seconds", 0)
    assert tasks.reconcile_pending_payments.run() == {"payments": 1, "refunds": 0, "errors": 1}
    assert order.status == OrderStatus.PAYMENT_PENDING and db.query(FailedJob).count() == 0


def processing_refund(db, order, amount=1000):
    from app.services.orders import OrderService
    from app.services.payment_timing import database_now

    deliver(db, order, amount=amount)
    OrderService(db).cancel_order(order.id, order.customer)
    refund = db.query(Refund).one()
    refund.status, refund.attempts = "processing", 1
    refund.updated_at = database_now(db) - timedelta(minutes=10)
    db.commit()
    return refund


@pytest.mark.parametrize("status", ["processed", "failed", "pending"])
def test_processing_refund_is_fetched_and_terminal_outcome_uses_inbound_pipeline(db, polling, monkeypatch, status):
    (order, payment, stock), gateway, calls, _ = polling
    refund = processing_refund(db, order)
    refund.provider_refund_id = "rfnd_known"
    db.commit()
    monkeypatch.setattr(gateway, "fetch_refund", lambda reference: ProviderRefund(reference, status))
    assert tasks.reconcile_pending_payments.run() == {"payments": 0, "refunds": 1, "errors": 0}
    assert refund.attempts == 1 and payment.status == PaymentStatus.AUTHORIZED
    drain_receipts(db)
    assert refund.status == ("succeeded" if status == "processed" else "failed" if status == "failed" else "processing")
    assert payment.status == (PaymentStatus.REFUNDED if status == "processed" else PaymentStatus.AUTHORIZED)
    assert db.query(FailedJob).count() == (1 if status == "failed" else 0)
    assert (stock.available_quantity, stock.reserved_quantity) == (10, 0)
    assert not calls


def test_unknown_refund_outcome_is_looked_up_before_requeueing_same_obligation(db, polling, monkeypatch):
    (order, payment, _), gateway, calls, _ = polling
    refund = processing_refund(db, order)
    original_key = refund.idempotency_key
    assert tasks.reconcile_pending_payments.run() == {"payments": 0, "refunds": 1, "errors": 0}
    assert calls == [("find", original_key)] and refund.status == "requested"
    assert refund.attempts == 1 and refund.idempotency_key == original_key
    assert db.query(OutboxEvent).filter_by(event_type="refund.requested").count() == 2
    monkeypatch.setattr("app.services.refunds.resolve_refund_provider", lambda *args: gateway)
    tasks.process_refund.run(str(refund.id))
    db.refresh(refund)
    db.refresh(payment)
    assert calls == [("find", original_key), ("find", original_key), ("submit", original_key)]
    assert refund.status == "succeeded" and refund.attempts == 2 and payment.status == PaymentStatus.REFUNDED


def test_processing_refund_active_lease_is_not_polled(db, polling):
    from app.services.payment_timing import database_now

    (order, _, _), _, calls, _ = polling
    refund = processing_refund(db, order)
    refund.claim_token, refund.lease_until = uuid4(), database_now(db) + timedelta(minutes=5)
    db.commit()
    assert tasks.reconcile_pending_payments.run() == {"payments": 0, "refunds": 0, "errors": 0}
    assert calls == [] and refund.attempts == 1


def test_unknown_refund_at_attempt_limit_is_not_resubmitted(db, polling, monkeypatch):
    from app.core.config import get_settings

    (order, payment, _), _, calls, _ = polling
    refund = processing_refund(db, order)
    monkeypatch.setattr(get_settings(), "task_max_retries", 0)
    assert tasks.reconcile_pending_payments.run()["refunds"] == 1
    assert refund.status == "failed" and len(calls) == 1 and calls[0][0] == "find"
    assert db.query(FailedJob).count() == 1 and payment.status == PaymentStatus.AUTHORIZED


def test_bounded_polling_is_fair_to_unvisited_sessions(db, polling, monkeypatch):
    from app.core.config import get_settings
    from app.models.entities import Order, PaymentReconciliation

    (order, _, _), _, _, results = polling
    results.clear()
    for _ in range(3):
        other = Order(
            tenant_id=order.tenant_id,
            principal_scope="tenant",
            idempotency_key=uuid4().hex,
            status=OrderStatus.PAYMENT_PENDING,
            total_amount=10,
        )
        db.add(other)
        db.flush()
        db.add(Payment(order_id=other.id, provider="razorpay", provider_reference="order_" + uuid4().hex, amount=10))
    db.commit()
    monkeypatch.setattr(get_settings(), "payment_maintenance_batch_size", 2)
    assert tasks.reconcile_pending_payments.run()["payments"] == 2
    assert tasks.reconcile_pending_payments.run()["payments"] == 2
    assert db.query(PaymentReconciliation).count() == 4


def test_unknown_refund_found_by_lookup_is_not_resubmitted(db, polling, monkeypatch):
    (order, payment, _), gateway, calls, _ = polling
    refund = processing_refund(db, order)
    monkeypatch.setattr(gateway, "find_refund", lambda *args: ProviderRefund("rfnd_found", "processed"))
    assert tasks.reconcile_pending_payments.run()["refunds"] == 1
    assert refund.status == "processing" and refund.provider_refund_id == "rfnd_found"
    drain_receipts(db)
    assert refund.status == "succeeded" and payment.status == PaymentStatus.REFUNDED and calls == []
    assert db.query(OutboxEvent).filter_by(event_type="refund.requested").count() == 1


@pytest.mark.parametrize("outcome", ["mismatch", "unavailable", "invalid", "missing_payment_id"])
def test_refund_poll_failures_preserve_the_obligation(db, polling, monkeypatch, caplog, outcome):
    (order, payment, _), gateway, _, _ = polling
    refund = processing_refund(db, order)
    refund.provider_refund_id = "rfnd_expected"
    if outcome == "missing_payment_id":
        payment.provider_transaction_id = None
    db.commit()
    if outcome == "unavailable":

        def fail(reference):
            raise ProviderUnavailable("synthetic-private-provider-data")

        monkeypatch.setattr(gateway, "fetch_refund", fail)
    elif outcome == "mismatch":
        monkeypatch.setattr(gateway, "fetch_refund", lambda reference: ProviderRefund("rfnd_wrong", "processed"))
    elif outcome == "invalid":
        monkeypatch.setattr(gateway, "fetch_refund", lambda reference: ProviderRefund(reference, "unknown"))
    assert tasks.reconcile_pending_payments.run() == {"payments": 0, "refunds": 1, "errors": 1}
    assert refund.status == "processing" and refund.attempts == 1 and refund.provider_refund_id == "rfnd_expected"
    assert refund.last_error == "provider_unavailable" and refund.claim_token is None
    assert db.query(FailedJob).count() == 0 and "synthetic-private-provider-data" not in caplog.text


def test_zero_refund_recovery_uses_the_local_worker_without_inventing_provider_identity(db, polling, monkeypatch):
    (order, payment, _), _, calls, _ = polling
    order.total_amount = payment.amount = order.items[0].unit_price = 0
    db.commit()
    refund = processing_refund(db, order, amount=0)
    assert tasks.reconcile_pending_payments.run()["refunds"] == 1
    assert refund.status == "requested" and refund.provider_refund_id is None and calls == []
    monkeypatch.setattr(
        "app.services.refunds.resolve_refund_provider", lambda *a: pytest.fail("Zero money provider call")
    )
    tasks.process_refund.run(str(refund.id))
    db.refresh(refund)
    assert refund.status == "succeeded" and refund.provider_refund_id is None


def test_stale_poll_observation_remains_durable_without_clobbering_replacement_lease(db, polling, monkeypatch):
    from app.models.entities import PaymentReconciliation
    from app.services.payment_timing import database_now

    (order, payment, _), gateway, _, results = polling
    replacement = uuid4()

    def replace_claim(reference):
        checkpoint = db.get(PaymentReconciliation, payment.id)
        checkpoint.claim_token, checkpoint.lease_until = replacement, database_now(db) + timedelta(minutes=5)
        db.commit()
        return results

    monkeypatch.setattr(gateway, "fetch_payment_status", replace_claim)
    assert tasks.reconcile_pending_payments.run()["payments"] == 1
    checkpoint = db.get(PaymentReconciliation, payment.id)
    assert checkpoint.claim_token == replacement
    drain_receipts(db)
    assert order.status == OrderStatus.PAID


def test_poll_journal_commit_failure_keeps_recoverable_lease_without_partial_receipt(db, polling, monkeypatch):
    from app.models.entities import PaymentReconciliation
    from app.repositories.outbox import OutboxRepository
    from app.services.payment_timing import database_now

    (order, payment, _), _, calls, _ = polling
    original = OutboxRepository.add

    def fail(repo, event, queue):
        if event.event_type == "payment.provider_event":
            raise RuntimeError("synthetic journal failure")
        return original(repo, event, queue)

    monkeypatch.setattr(OutboxRepository, "add", fail)
    with pytest.raises(RuntimeError, match="synthetic journal failure"):
        tasks.reconcile_pending_payments.run()
    checkpoint = db.get(PaymentReconciliation, payment.id)
    assert checkpoint.claim_token is not None and db.query(InboundWebhookEvent).count() == 0
    assert order.status == OrderStatus.PAYMENT_PENDING and db.query(FailedJob).count() == 0
    checkpoint.lease_until = database_now(db) - timedelta(seconds=1)
    db.commit()
    monkeypatch.setattr(OutboxRepository, "add", original)
    assert tasks.reconcile_pending_payments.run()["payments"] == 1 and len(calls) == 2
    drain_receipts(db)
    assert order.status == OrderStatus.PAID and db.query(InboundWebhookEvent).count() == 1


def test_foreign_poll_checkpoint_cannot_be_used_for_financial_observations(db, polling):
    from app.models.entities import PaymentReconciliation, Tenant

    (order, payment, _), _, calls, _ = polling
    foreign = Tenant(name="Foreign checkpoint", slug="foreign-checkpoint")
    db.add(foreign)
    db.flush()
    db.add(PaymentReconciliation(payment_id=payment.id, order_id=order.id, tenant_id=foreign.id))
    db.commit()
    with pytest.raises(ProviderUnavailable, match="ownership"):
        tasks.reconcile_pending_payments.run()
    assert order.status == OrderStatus.PAYMENT_PENDING and calls == []
    assert db.query(InboundWebhookEvent).count() == 0
