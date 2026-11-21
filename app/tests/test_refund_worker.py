from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from conftest import TestingSessionLocal
from test_consumers import consume

from app.core.exceptions import TransientError
from app.events.contracts import EventNames
from app.models.entities import AuditLog, FailedJob, OutboxEvent, Payment, Refund
from app.models.enums import PaymentStatus
from app.providers.contracts import ProviderRefund, ProviderUnavailable
from app.services.orders import OrderService


@pytest.fixture
def refund_request(db, workflow):
    from app.models.entities import User

    order, stock = workflow
    order.total_amount = Decimal("10.00")
    db.commit()
    consume(order, EventNames.ORDER_CREATED)
    consume(order, EventNames.INVENTORY_RESERVED)
    consume(order, EventNames.PAYMENT_AUTHORIZED)
    OrderService(db).cancel_order(order.id, db.get(User, order.customer_id))
    refund = db.query(Refund).one()
    return refund.id, order.id, stock.id


def test_cancellation_journals_refund_before_any_provider_call(db, refund_request):
    from app.models.entities import Inventory, Order, Shipment, ShipmentEvent
    from app.models.enums import OrderStatus, ShipmentStatus
    from app.workers.tasks import process_refund

    refund_id, order_id, stock_id = refund_request
    row = db.get(Refund, refund_id)
    payment = db.get(Payment, row.payment_id)
    assert db.get(Order, order_id).status == OrderStatus.CANCELLED
    stock = db.get(Inventory, stock_id)
    assert (stock.available_quantity, stock.reserved_quantity) == (10, 0)
    shipment = db.query(Shipment).filter_by(order_id=order_id).one()
    assert shipment.status == ShipmentStatus.CANCELLED
    assert db.query(ShipmentEvent).filter_by(shipment_id=shipment.id, status=ShipmentStatus.CANCELLED).count() == 1
    assert db.query(AuditLog).filter_by(entity_id=shipment.id, action="shipment_cancelled").count() == 1
    assert row.status == "requested" and payment.status == PaymentStatus.AUTHORIZED
    assert db.query(OutboxEvent).filter_by(event_type="refund.requested").count() == 1
    process_refund.run(str(refund_id))
    db.expire_all()
    assert row.status == "succeeded" and payment.status == PaymentStatus.REFUNDED
    process_refund.run(str(refund_id))
    assert row.attempts == 1
    assert db.query(AuditLog).filter_by(action="payment_refunded").count() == 1
    assert db.query(OutboxEvent).filter_by(event_type="refund.succeeded").count() == 1


def test_cancellation_rolls_back_inventory_shipment_and_refund_together(db, workflow, monkeypatch):
    from app.models.entities import ShipmentEvent, User
    from app.models.enums import OrderStatus, ShipmentStatus
    from app.services.shipping import ShippingService

    order, stock = workflow
    for kind in (EventNames.ORDER_CREATED, EventNames.INVENTORY_RESERVED, EventNames.PAYMENT_AUTHORIZED):
        consume(order, kind)
    original = ShippingService.cancel_for_order

    def fail_after_shipment_cancel(service, current):
        original(service, current)
        service.db.flush()
        raise RuntimeError("synthetic cancellation failure")

    monkeypatch.setattr(ShippingService, "cancel_for_order", fail_after_shipment_cancel)
    with pytest.raises(RuntimeError, match="synthetic cancellation failure"):
        OrderService(db).cancel_order(order.id, db.get(User, order.customer_id))
    db.expire_all()
    assert order.status == OrderStatus.FULFILLMENT_REQUESTED
    assert (stock.available_quantity, stock.reserved_quantity) == (9, 1)
    assert order.shipments[0].status == ShipmentStatus.CREATED
    assert db.query(ShipmentEvent).filter_by(status=ShipmentStatus.CANCELLED).count() == 0
    assert db.query(AuditLog).filter_by(action="shipment_cancelled").count() == 0
    assert db.query(Refund).count() == 0
    assert db.query(OutboxEvent).filter_by(event_type="refund.requested").count() == 0


def test_refund_retry_looks_up_unknown_outcome_before_reissuing(db, refund_request):
    from app.services.refunds import run_refund

    refund_id, _, _ = refund_request
    calls = []

    class Gateway:
        def refund(self, payment, amount, key):
            calls.append(("refund", key))
            raise ProviderUnavailable("synthetic-sensitive-message")

        def find_refund(self, payment, amount, key):
            calls.append(("find", key))
            return ProviderRefund("rfnd_recovered", "processed")

    with patch("app.services.refunds.resolve_refund_provider", return_value=Gateway()):
        with pytest.raises(TransientError):
            run_refund(TestingSessionLocal, refund_id)
        db.expire_all()
        row = db.get(Refund, refund_id)
        assert row.status == "requested" and row.attempts == 1
        assert "synthetic-sensitive" not in (row.last_error or "")
        run_refund(TestingSessionLocal, refund_id)
    db.expire_all()
    assert row.status == "succeeded" and row.provider_refund_id == "rfnd_recovered"
    assert [name for name, _ in calls] == ["refund", "find"]
    assert calls[0][1] == calls[1][1] == row.idempotency_key


def test_refund_exhaustion_is_durable_and_sanitized(db, refund_request):
    from app.core.config import get_settings
    from app.services.refunds import RefundExhausted, run_refund

    refund_id, _, _ = refund_request

    class Gateway:
        def refund(self, *args):
            raise ProviderUnavailable("synthetic-sensitive-message")

    with (
        patch(
            "app.services.refunds.get_settings", return_value=get_settings().model_copy(update={"task_max_retries": 0})
        ),
        patch("app.services.refunds.resolve_refund_provider", return_value=Gateway()),
    ):
        with pytest.raises(RefundExhausted):
            run_refund(TestingSessionLocal, refund_id)
    db.expire_all()
    row = db.get(Refund, refund_id)
    assert row.status == "failed" and row.attempts == 1
    job = db.query(FailedJob).one()
    assert job.tenant_id == row.tenant_id and job.aggregate_id == row.id
    assert "synthetic-sensitive" not in job.error_message + (row.last_error or "")
    assert db.get(Payment, row.payment_id).status == PaymentStatus.AUTHORIZED
    assert db.query(AuditLog).filter_by(entity_id=row.id, action="refund_failed").count() == 1
    from app.workers.tasks import process_refund

    process_refund.on_failure(RefundExhausted("repeat"), "task", (str(refund_id),), {}, None)
    assert db.query(FailedJob).count() == 1
    assert db.query(AuditLog).filter_by(entity_id=row.id, action="refund_failed").count() == 1


@pytest.mark.parametrize("status", ["pending", "created"])
def test_refund_pending_is_not_success_and_retry_fetches_known_reference(db, refund_request, status):
    from app.services.refunds import run_refund

    refund_id, _, _ = refund_request
    calls = []

    class Gateway:
        def refund(self, *args):
            calls.append("refund")
            return ProviderRefund("rfnd_pending", status)

        def fetch_refund(self, reference):
            assert reference == "rfnd_pending"
            calls.append("fetch")
            return ProviderRefund(reference, "processed")

    with patch("app.services.refunds.resolve_refund_provider", return_value=Gateway()):
        run_refund(TestingSessionLocal, refund_id)
        db.expire_all()
        row = db.get(Refund, refund_id)
        assert row.status == "processing" and row.claim_token is None
        assert db.get(Payment, row.payment_id).status == PaymentStatus.AUTHORIZED
        run_refund(TestingSessionLocal, refund_id)
    db.expire_all()
    assert row.status == "succeeded" and calls == ["refund", "fetch"]


@pytest.mark.parametrize("outcome", ["declined", "failed", "invalid"])
def test_refund_terminal_errors_do_not_mark_payment_refunded(db, refund_request, outcome):
    from app.core.config import get_settings
    from app.core.exceptions import BusinessError
    from app.providers.contracts import ProviderDeclined
    from app.services.refunds import run_refund

    refund_id, _, _ = refund_request

    class Gateway:
        def refund(self, *args):
            if outcome == "declined":
                raise ProviderDeclined("synthetic-sensitive-message")
            return ProviderRefund("rfnd_terminal", "failed" if outcome == "failed" else "unsupported")

    with (
        patch("app.services.refunds.resolve_refund_provider", return_value=Gateway()),
        patch(
            "app.services.refunds.get_settings", return_value=get_settings().model_copy(update={"task_max_retries": 0})
        ),
    ):
        if outcome == "failed":
            run_refund(TestingSessionLocal, refund_id)
        else:
            with pytest.raises(BusinessError) as error:
                run_refund(TestingSessionLocal, refund_id)
            assert error.value.__suppress_context__
    db.expire_all()
    row = db.get(Refund, refund_id)
    assert row.status == "failed" and row.failed_job_id is not None
    assert db.get(Payment, row.payment_id).status == PaymentStatus.AUTHORIZED
    assert "synthetic-sensitive" not in row.last_error + db.query(FailedJob).one().error_message


def test_unknown_refund_is_noop_and_bad_configuration_is_durable(db, refund_request):
    from uuid import uuid4

    from app.providers.resolution import PaymentNotConfigured
    from app.services.refunds import RefundExhausted, run_refund

    run_refund(TestingSessionLocal, uuid4())
    with patch("app.services.refunds.resolve_refund_provider", side_effect=PaymentNotConfigured("synthetic-sensitive")):
        with pytest.raises(RefundExhausted):
            run_refund(TestingSessionLocal, refund_request[0])
    db.expire_all()
    assert db.get(Refund, refund_request[0]).last_error == "refund_configuration_unavailable"
    assert db.query(FailedJob).count() == 1


@pytest.mark.postgres
def test_postgres_refund_claim_deduplicates_and_releases_all_locks_during_provider_call(db, refund_request):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import select

    from app.models.entities import Inventory, Order
    from app.services.refunds import RefundBusy, run_refund

    refund_id, order_id, stock_id = refund_request
    entered, release = Event(), Event()
    calls = []

    class Gateway:
        def refund(self, payment, amount, key):
            calls.append(key)
            entered.set()
            if not release.wait(15):
                raise RuntimeError("Provider probe timeout")
            return ProviderRefund("rfnd_concurrent", "processed")

    db.rollback()
    with patch("app.services.refunds.resolve_refund_provider", return_value=Gateway()):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_refund, TestingSessionLocal, refund_id)
            try:
                assert entered.wait(15)
                with TestingSessionLocal() as observer:
                    assert (
                        observer.scalar(select(Order.id).where(Order.id == order_id).with_for_update(nowait=True))
                        == order_id
                    )
                    assert (
                        observer.scalar(select(Refund.id).where(Refund.id == refund_id).with_for_update(nowait=True))
                        == refund_id
                    )
                    assert (
                        observer.scalar(
                            select(Inventory.id).where(Inventory.id == stock_id).with_for_update(nowait=True)
                        )
                        == stock_id
                    )
                with pytest.raises(RefundBusy):
                    run_refund(TestingSessionLocal, refund_id)
            finally:
                release.set()
            future.result(timeout=15)
    db.expire_all()
    row = db.get(Refund, refund_id)
    assert row.status == "succeeded" and row.attempts == 1 and calls == [row.idempotency_key]
    assert db.query(OutboxEvent).filter_by(event_type="refund.succeeded").count() == 1
    print("REFUND PROVIDER IN FLIGHT: ORDER, REFUND AND INVENTORY NOWAIT PASS; ONE CLAIM/EFFECT")


def test_crash_after_provider_response_reconciles_before_second_submission(db, refund_request):
    from app.services.refunds import run_refund

    refund_id, _, _ = refund_request
    calls = []

    class Gateway:
        def refund(self, *args):
            calls.append("refund")
            return ProviderRefund("rfnd_committed_remote", "processed")

        def find_refund(self, *args):
            calls.append("find")
            return ProviderRefund("rfnd_committed_remote", "processed")

    opened = 0

    @contextmanager
    def broken_factory():
        nonlocal opened
        opened += 1
        with TestingSessionLocal() as session:
            if opened == 2:
                with patch.object(session, "commit", side_effect=RuntimeError("synthetic database failure")):
                    yield session
            else:
                yield session

    with patch("app.services.refunds.resolve_refund_provider", return_value=Gateway()):
        with pytest.raises(TransientError):
            run_refund(broken_factory, refund_id)
        db.expire_all()
        row = db.get(Refund, refund_id)
        assert row.status == "processing"
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
        run_refund(TestingSessionLocal, refund_id)
    db.expire_all()
    assert row.status == "succeeded" and calls == ["refund", "find"]


def test_zero_value_refund_completes_without_provider_request(db, workflow):
    from app.models.entities import User
    from app.workers.tasks import process_refund

    order, _ = workflow
    order.total_amount = Decimal("0.00")
    db.commit()
    consume(order, EventNames.ORDER_CREATED)
    consume(order, EventNames.INVENTORY_RESERVED)
    OrderService(db).cancel_order(order.id, db.get(User, order.customer_id))
    row = db.query(Refund).one()
    assert row.amount == 0
    with patch(
        "app.services.refunds.resolve_refund_provider", side_effect=AssertionError("Provider must not be called")
    ):
        process_refund.run(str(row.id))
    db.refresh(row)
    assert row.status == "succeeded" and row.provider_refund_id is None
    assert db.get(Payment, row.payment_id).status == PaymentStatus.REFUNDED


@pytest.mark.parametrize("unavailable", [False, True])
def test_webhook_success_wins_over_late_worker_response(db, refund_request, unavailable):
    from app.services.refund_intents import succeed_refund
    from app.services.refunds import locked_refund, run_refund

    refund_id, _, _ = refund_request

    class Gateway:
        def refund(self, *args):
            with TestingSessionLocal() as session:
                row, payment = locked_refund(session, refund_id)
                succeed_refund(session, row, payment, "rfnd_race")
                session.commit()
            if unavailable:
                raise ProviderUnavailable("synthetic-sensitive-message")
            return ProviderRefund("rfnd_race", "processed")

    with patch("app.services.refunds.resolve_refund_provider", return_value=Gateway()):
        run_refund(TestingSessionLocal, refund_id)
    db.expire_all()
    assert db.get(Refund, refund_id).status == "succeeded"
    assert db.query(FailedJob).count() == 0
    assert db.query(OutboxEvent).filter_by(event_type="refund.succeeded").count() == 1


def test_stale_pending_result_does_not_replace_a_new_claim(db, refund_request):
    from uuid import uuid4

    from app.services.refunds import locked_refund, run_refund

    refund_id, _, _ = refund_request
    replacement = uuid4()

    class Gateway:
        def refund(self, *args):
            with TestingSessionLocal() as session:
                row, _ = locked_refund(session, refund_id)
                row.claim_token = replacement
                session.commit()
            return ProviderRefund("rfnd_stale", "pending")

    with patch("app.services.refunds.resolve_refund_provider", return_value=Gateway()):
        run_refund(TestingSessionLocal, refund_id)
    db.expire_all()
    row = db.get(Refund, refund_id)
    assert row.claim_token == replacement and row.provider_refund_id is None and row.status == "processing"


def test_refund_provider_snapshot_does_not_switch_to_new_merchant_configuration(db, refund_request):
    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    from app.core.config import get_settings
    from app.core.crypto import CredentialVault
    from app.models.entities import TenantPaymentConfig
    from app.providers.resolution import PaymentNotConfigured, resolve_refund_provider

    row = db.get(Refund, refund_request[0])
    settings = get_settings().model_copy(
        update={"credentials_encryption_key": SecretStr(Fernet.generate_key().decode())}
    )
    token = CredentialVault(settings.credentials_encryption_key).encrypt(SecretStr("synthetic"))
    config = TenantPaymentConfig(
        tenant_id=row.tenant_id,
        provider="razorpay",
        mode="test",
        key_id="public1234",
        key_secret_enc=token,
        webhook_secret_enc=token,
        webhook_public_id="public-refund-config",
        is_active=True,
    )
    db.add(config)
    db.commit()
    assert resolve_refund_provider(db, row.tenant_id, "mock", settings).name == "mock"
    config.is_active = False
    db.commit()
    assert resolve_refund_provider(db, row.tenant_id, "razorpay", settings).name == "razorpay"
    for name in ["unknown", None]:
        with pytest.raises(PaymentNotConfigured):
            resolve_refund_provider(db, row.tenant_id, name, settings)
    with pytest.raises(PaymentNotConfigured):
        resolve_refund_provider(db, row.tenant_id, "mock", settings.model_copy(update={"allow_mock_providers": False}))


def test_recovery_waits_for_refund_success(db, workflow):
    from app.core.exceptions import ConflictError
    from app.events.contracts import DomainEvent
    from app.services.failed_jobs import record_failure, replay_failed_job
    from app.workers.tasks import process_refund

    order, _ = workflow
    consume(order, EventNames.ORDER_CREATED)
    consume(order, EventNames.INVENTORY_RESERVED)
    event = DomainEvent(
        event_type=EventNames.PAYMENT_AUTHORIZED, aggregate_id=order.id, tenant_id=order.tenant_id, payload={}
    )
    job = record_failure(db, event.model_dump(mode="json"), TransientError("offline"), 3, "shipments", "test")
    with pytest.raises(ConflictError) as error:
        replay_failed_job(db, job.id, actor=order.customer_id)
    assert error.value.code == "refund_pending"
    process_refund.run(str(db.query(Refund).one().id))
    replay_failed_job(db, job.id, actor=order.customer_id)
    assert order.status.value == "created"


def test_refund_publisher_routes_to_registered_refund_worker(monkeypatch):
    from uuid import uuid4

    from app.events.contracts import DomainEvent
    from app.events.publisher import EventPublisher
    from app.workers.celery_app import celery_app

    event = DomainEvent(event_type="refund.requested", aggregate_id=uuid4(), tenant_id=uuid4(), payload={})
    calls = []
    monkeypatch.setattr(celery_app, "send_task", lambda *args, **kwargs: calls.append((args, kwargs)))
    EventPublisher().publish(event, "payments")
    assert calls[0][0] == ("app.workers.tasks.process_refund",)
    assert calls[0][1]["args"] == [str(event.aggregate_id)]
    assert calls[0][1]["delivery_mode"] == 2 and calls[0][1]["queue"] == "payments"
