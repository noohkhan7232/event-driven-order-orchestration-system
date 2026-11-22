from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import TestingSessionLocal
from test_provider_events import pending_payment as pending_payment

from app.models.entities import AuditLog, Order, OrderItem, OrderTimeline, OutboxEvent, Refund
from app.models.enums import OrderStatus, PaymentStatus
from app.workers import tasks


@pytest.mark.parametrize("age,count", [(119, 0), (120, 1), (121, 1)])
def test_expiry_uses_capture_grace_and_compensates_once(db, pending_payment, monkeypatch, age, count):
    from app.services import payment_expiry
    from app.services.payment_timing import database_now

    order, payment, stock = pending_payment
    now = database_now(db)
    order.payment_expires_at = now - timedelta(seconds=age)
    db.commit()
    monkeypatch.setattr(payment_expiry, "database_now", lambda db: now)
    monkeypatch.setattr(
        "app.services.refunds.resolve_refund_provider", lambda *a: pytest.fail("Unexpected provider call")
    )
    assert tasks.expire_unpaid_orders.run() == count
    assert tasks.expire_unpaid_orders.run() == 0
    db.expire_all()
    if count:
        assert order.status == OrderStatus.FAILED and order.failure_reason == "PAYMENT_EXPIRED"
        assert payment.status == PaymentStatus.FAILED and payment.provider_status == "abandoned"
        assert (stock.available_quantity, stock.reserved_quantity) == (10, 0)
        assert db.query(AuditLog).filter_by(entity_id=order.id, action="inventory_released").count() == 1
        assert db.query(OrderTimeline).filter_by(order_id=order.id, to_status="failed").count() == 1
        notice = db.query(OutboxEvent).filter_by(event_type="payment.expired").one()
        assert notice.tenant_id == order.tenant_id and notice.payload["payload"]["reason"] == "PAYMENT_EXPIRED"
        changed = db.query(OutboxEvent).filter_by(event_type="order.status_changed").all()
        assert [row.payload["payload"]["reason"] for row in changed if row.payload["payload"]["to"] == "failed"] == [
            "PAYMENT_EXPIRED"
        ]
    else:
        assert order.status == OrderStatus.PAYMENT_PENDING and payment.status == PaymentStatus.INITIATED
        assert (stock.available_quantity, stock.reserved_quantity) == (9, 1)
    assert db.query(Refund).count() == 0


@pytest.mark.parametrize("excluded", ["no_expiry", "cod", "paid"])
def test_expiry_does_not_touch_ineligible_orders(db, pending_payment, excluded):
    from app.services.payment_timing import database_now

    order, payment, stock = pending_payment
    order.payment_expires_at = database_now(db) - timedelta(hours=1)
    if excluded == "no_expiry":
        order.payment_expires_at = None
    elif excluded == "cod":
        order.payment_method = "cod"
    else:
        order.status = OrderStatus.PAID
    db.commit()
    assert tasks.expire_unpaid_orders.run() == 0
    db.expire_all()
    assert order.failure_reason is None and payment.status == PaymentStatus.INITIATED
    assert (stock.available_quantity, stock.reserved_quantity) == (9, 1)


def test_expiry_failure_rolls_back_all_compensation_and_intents(db, pending_payment, monkeypatch):
    from app.repositories.outbox import OutboxRepository
    from app.services.payment_timing import database_now

    order, payment, stock = pending_payment
    order.payment_expires_at = database_now(db) - timedelta(hours=1)
    db.commit()
    original = OutboxRepository.add

    def fail(repo, event, queue):
        if event.event_type == "payment.expired":
            raise RuntimeError("synthetic expiry failure")
        return original(repo, event, queue)

    monkeypatch.setattr(OutboxRepository, "add", fail)
    with pytest.raises(RuntimeError, match="synthetic expiry failure"):
        tasks.expire_unpaid_orders.run()
    db.expire_all()
    assert order.status == OrderStatus.PAYMENT_PENDING and payment.status == PaymentStatus.INITIATED
    assert (stock.available_quantity, stock.reserved_quantity) == (9, 1)
    assert db.query(OrderTimeline).filter_by(to_status="failed").count() == 0
    assert db.query(AuditLog).filter_by(action="inventory_released").count() == 0
    assert db.query(OutboxEvent).filter_by(event_type="payment.expired").count() == 0


def test_expiry_keyset_batch_is_bounded_and_resumes_without_missing_rows(db, pending_payment, monkeypatch):
    from app.core.config import get_settings
    from app.services.payment_timing import database_now

    order, _, _ = pending_payment
    deadline = database_now(db) - timedelta(hours=1)
    order.payment_expires_at = deadline
    for _ in range(3):
        db.add(
            Order(
                tenant_id=order.tenant_id,
                principal_scope="tenant",
                idempotency_key=uuid4().hex,
                status=OrderStatus.PAYMENT_PENDING,
                payment_expires_at=deadline,
                total_amount=10,
            )
        )
    db.commit()
    monkeypatch.setattr(get_settings(), "payment_maintenance_batch_size", 2)
    assert [tasks.expire_unpaid_orders.run() for _ in range(3)] == [2, 2, 0]
    db.expire_all()
    assert db.query(Order).filter_by(status=OrderStatus.FAILED).count() == 4


def test_expiry_preserves_the_existing_mismatch_refund_obligation(db, pending_payment):
    from test_provider_events import deliver

    from app.services.payment_timing import database_now

    order, payment, stock = pending_payment
    deliver(db, order, amount=2000)
    original = db.query(Refund).one()
    order.payment_expires_at = database_now(db) - timedelta(hours=1)
    db.commit()
    assert tasks.expire_unpaid_orders.run() == 1
    db.expire_all()
    assert order.status == OrderStatus.FAILED and payment.status == PaymentStatus.FAILED
    assert (stock.available_quantity, stock.reserved_quantity) == (10, 0)
    assert db.query(Refund).one().id == original.id and original.reason == "AMOUNT_MISMATCH"
    assert db.query(OutboxEvent).filter_by(event_type="refund.requested").count() == 1


@pytest.mark.postgres
def test_postgres_two_expiry_schedulers_skip_locked_and_release_stock_once(db, pending_payment, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from app.core.config import get_settings
    from app.services.inventory import InventoryService
    from app.services.payment_expiry import expire_orders
    from app.services.payment_timing import database_now

    order, _, stock = pending_payment
    deadline = database_now(db) - timedelta(hours=1)
    order.payment_expires_at = deadline
    other = Order(
        tenant_id=order.tenant_id,
        principal_scope="tenant",
        idempotency_key="other-expiry",
        status=OrderStatus.PAYMENT_PENDING,
        payment_expires_at=deadline,
        total_amount=10,
    )
    other.items.append(OrderItem(sku=order.items[0].sku, quantity=1, unit_price=10, product_name="Product"))
    db.add(other)
    db.flush()
    InventoryService(db).reserve_for_order(other)
    db.commit()
    first_id = min(order.id, other.id)
    entered, release = Event(), Event()
    original = InventoryService.release_for_order

    def block(service, current):
        if current.id == first_id:
            entered.set()
            assert release.wait(15)
        return original(service, current)

    monkeypatch.setattr(get_settings(), "payment_maintenance_batch_size", 1)
    monkeypatch.setattr(InventoryService, "release_for_order", block)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(expire_orders, TestingSessionLocal)
        try:
            assert entered.wait(15)
            assert expire_orders(TestingSessionLocal) == 1
        finally:
            release.set()
        assert future.result(timeout=15) == 1
    db.expire_all()
    assert order.status == other.status == OrderStatus.FAILED
    assert (stock.available_quantity, stock.reserved_quantity) == (10, 0)
    assert db.query(OrderTimeline).filter_by(to_status="failed").count() == 2
    assert db.query(AuditLog).filter_by(action="inventory_released").count() == 2
    print("TWO EXPIRY SCHEDULERS: SKIP LOCKED; TWO ORDERS EXPIRED ONCE; STOCK CONSERVED")
