"""These tests require a dedicated real PostgreSQL TEST_DATABASE_URL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from uuid import uuid4

import pytest
from conftest import TestingSessionLocal, engine
from sqlalchemy import select
from test_consumers import consume

from app.events.contracts import DomainEvent, EventNames
from app.models.entities import Order, OrderItem, OutboxEvent, Payment, Shipment
from app.models.enums import OrderStatus
from app.repositories.outbox import OutboxRepository
from app.services.orders import OrderService
from app.workers import tasks

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def require_real_postgres():
    assert engine.dialect.name == "postgresql", "TEST_DATABASE_URL must select real PostgreSQL"


def test_postgres_50_reservations_against_stock_10(db, workflow, monkeypatch):
    order, stock = workflow
    orders = [order]
    for _ in range(49):
        new = Order(customer_id=order.customer_id, idempotency_key=str(uuid4()), status=OrderStatus.CREATED)
        new.items.append(OrderItem(sku="SKU", product_name="Product", quantity=1, unit_price=10))
        db.add(new)
        orders.append(new)
    db.commit()
    events = [DomainEvent(event_type=EventNames.ORDER_CREATED, aggregate_id=o.id, payload={}) for o in orders]
    monkeypatch.setattr(tasks, "SessionLocal", TestingSessionLocal)
    barrier = Barrier(50)

    def run(event):
        barrier.wait(timeout=30)
        tasks.process_event.run(event.model_dump(mode="json"))

    with ThreadPoolExecutor(max_workers=50) as pool:
        list(pool.map(run, events))
    db.expire_all()
    assert db.query(Order).filter_by(status=OrderStatus.INVENTORY_RESERVED).count() == 10
    assert db.query(Order).filter_by(status=OrderStatus.FAILED).count() == 40
    assert stock.available_quantity == 0 and stock.reserved_quantity == 10
    assert stock.available_quantity + stock.reserved_quantity == 10
    print("50-vs-10: reserved=10 failed=40 available=0 reserved_quantity=10 conserved=10")


def test_postgres_concurrent_duplicate_delivery(db, workflow, monkeypatch):
    order, stock = workflow
    monkeypatch.setattr(tasks, "SessionLocal", TestingSessionLocal)
    barrier = Barrier(8)
    for kind in [EventNames.ORDER_CREATED, EventNames.INVENTORY_RESERVED, EventNames.PAYMENT_AUTHORIZED]:
        event = DomainEvent(event_type=kind, aggregate_id=order.id, payload={}).model_dump(mode="json")

        def run(_, event=event):
            barrier.wait(timeout=30)
            tasks.process_event.run(event)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(run, range(8)))
    db.expire_all()
    assert stock.available_quantity == 9 and stock.reserved_quantity == 1
    assert db.query(Payment).count() == db.query(Shipment).count() == 1


def test_postgres_cancel_vs_reserve_conserves_stock(db, workflow, monkeypatch):
    order, stock = workflow
    order_id, user_id = order.id, order.customer_id
    db.rollback()
    monkeypatch.setattr(tasks, "SessionLocal", TestingSessionLocal)
    barrier = Barrier(2)

    def reserve():
        barrier.wait(timeout=30)
        with TestingSessionLocal() as session:
            current = session.get(Order, order_id)
            consume(current, EventNames.ORDER_CREATED)

    def cancel():
        from app.models.entities import User

        barrier.wait(timeout=30)
        with TestingSessionLocal() as session:
            OrderService(session).cancel_order(order_id, session.get(User, user_id))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve), pool.submit(cancel)]
        for future in futures:
            future.result(timeout=30)
    db.expire_all()
    assert order.status == OrderStatus.CANCELLED
    assert stock.available_quantity == 10 and stock.reserved_quantity == 0


def test_postgres_two_relays_claim_disjoint_rows(db, monkeypatch):
    from app.workers.outbox_relay import relay_once

    for _ in range(12):
        OutboxRepository(db).add(DomainEvent(event_type="test", aggregate_id=uuid4(), payload={}), "orders")
    db.commit()
    sent, mutex = [], Lock()
    barrier = Barrier(2)

    def send(*args, **kwargs):
        with mutex:
            sent.append(kwargs["task_id"])

    monkeypatch.setattr("app.events.publisher.celery_app.send_task", send)

    def relay(_):
        barrier.wait(timeout=30)
        with TestingSessionLocal() as session:
            return relay_once(session, 6)

    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(relay, range(2)))
    assert sum(counts) == len(sent) == len(set(sent)) == 12
    with engine.connect() as connection:
        assert len(connection.scalars(select(OutboxEvent.id).where(OutboxEvent.status == "published")).all()) == 12


def test_postgres_inventory_check_constraints_are_enforced(db, workflow):
    from sqlalchemy import inspect
    from sqlalchemy.exc import IntegrityError

    _, stock = workflow
    checks = {row["name"] for row in inspect(engine).get_check_constraints("inventory")}
    assert {"ck_inventory_available_non_negative", "ck_inventory_reserved_non_negative"} <= checks
    for column in ["available_quantity", "reserved_quantity"]:
        with pytest.raises(IntegrityError), db.begin_nested():
            setattr(stock, column, -1)
            db.flush()
    db.refresh(stock)
    assert stock.available_quantity == 10 and stock.reserved_quantity == 0
