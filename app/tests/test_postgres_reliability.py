"""These tests require a dedicated real PostgreSQL TEST_DATABASE_URL."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from uuid import uuid4

import pytest
from conftest import DEFAULT_TENANT_ID, TestingSessionLocal, engine
from sqlalchemy import select
from test_consumers import consume

from app.events.contracts import DomainEvent, EventNames
from app.models.entities import Order, OrderItem, OutboxEvent, Payment, Shipment
from app.models.enums import OrderStatus
from app.repositories.outbox import OutboxRepository
from app.services.orders import OrderService
from app.workers import tasks

pytestmark = pytest.mark.postgres


def test_postgres_adjustments_and_thirty_reservations_conserve_stock(db, workflow, monkeypatch):
    from app.api.v1.endpoints.inventory import adjust_inventory
    from app.auth.principal import Principal
    from app.core.exceptions import DomainError
    from app.models.entities import User
    from app.models.enums import UserRole
    from app.schemas.inventory import InventoryAdjustment

    initial, stock = workflow
    stock.available_quantity = 20
    user = db.get(User, initial.customer_id)
    user.role = UserRole.VENDOR
    db.commit()
    events = []
    for index in range(30):
        order = Order(
            tenant_id=initial.tenant_id,
            customer_id=user.id,
            principal_scope=f"user:{user.id}",
            idempotency_key=f"race-{index}",
            status=OrderStatus.CREATED,
        )
        order.items.append(OrderItem(sku="SKU", product_name="Snapshot", quantity=1, unit_price=10))
        db.add(order)
        db.flush()
        events.append(
            DomainEvent(
                event_type=EventNames.ORDER_CREATED, aggregate_id=order.id, tenant_id=order.tenant_id, payload={}
            ).model_dump(mode="json")
        )
    db.commit()
    monkeypatch.setattr(tasks, "SessionLocal", TestingSessionLocal)
    actions = []
    for index, event in enumerate(events):
        if index < 20:
            actions.append(("adjust", 4 if index % 2 == 0 else -2))
        actions.append(("reserve", event))

    def run(action):
        kind, value = action
        if kind == "reserve":
            tasks.process_event.run(value)
            return 0
        with TestingSessionLocal() as session:
            try:
                adjust_inventory(
                    stock.id,
                    InventoryAdjustment(delta=value, reason="Concurrent count"),
                    session,
                    Principal(initial.tenant_id, user=user),
                )
                return value
            except DomainError as error:
                assert error.code == "negative_stock"
                return 0

    with ThreadPoolExecutor(max_workers=20) as pool:
        accepted = sum(pool.map(run, actions))
    db.expire_all()
    reserved = db.query(Order).filter(Order.id != initial.id, Order.inventory_reserved.is_(True)).count()
    assert stock.available_quantity == 20 + accepted - reserved
    assert stock.reserved_quantity == reserved
    assert stock.available_quantity >= 0 and reserved <= 30
    print(
        f"CONSERVATION PASS: initial=20 accepted_delta={accepted} "
        f"reserved={reserved} available={stock.available_quantity}"
    )


def test_postgres_concurrent_double_replay_has_one_fresh_event(db, workflow):
    from app.core.exceptions import ConflictError, TransientError
    from app.services.failed_jobs import record_failure, replay_failed_job

    order, _ = workflow
    event = DomainEvent(
        event_type=EventNames.ORDER_CREATED, aggregate_id=order.id, tenant_id=order.tenant_id, payload={}
    )
    job = record_failure(db, event.model_dump(mode="json"), TransientError("offline"), 3, "orders", "test")
    job_id, actor, tenant_id = job.id, order.customer_id, order.tenant_id
    gate = Barrier(2)

    def replay(_):
        gate.wait(timeout=30)
        with TestingSessionLocal() as session:
            try:
                replay_failed_job(session, job_id, actor=actor, tenant_id=tenant_id)
                return "replayed"
            except ConflictError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(replay, range(2)))
    assert sorted(results) == ["job_already_replayed", "replayed"]
    db.expire_all()
    rows = db.query(OutboxEvent).filter_by(aggregate_id=order.id, event_type=EventNames.ORDER_CREATED).all()
    assert len(rows) == 1 and rows[0].event_id != event.event_id


@pytest.fixture(autouse=True)
def require_real_postgres():
    assert engine.dialect.name == "postgresql", "TEST_DATABASE_URL must select real PostgreSQL"


def test_postgres_50_reservations_against_stock_10(db, workflow, monkeypatch):
    order, stock = workflow
    orders = [order]
    for _ in range(49):
        new = Order(
            tenant_id=DEFAULT_TENANT_ID,
            principal_scope=f"user:{order.customer_id}",
            customer_id=order.customer_id,
            idempotency_key=str(uuid4()),
            status=OrderStatus.CREATED,
        )
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


def test_postgres_two_tenants_same_sku_have_independent_stock(db, monkeypatch):
    from app.models.entities import Inventory, Tenant, User, Warehouse
    from app.models.enums import UserRole

    tenants = [Tenant(name=f"Store {i}", slug=f"concurrent-{i}") for i in range(2)]
    db.add_all(tenants)
    db.flush()
    stocks, events = [], []
    for index, tenant in enumerate(tenants):
        user = User(
            tenant_id=tenant.id,
            email=f"parallel-{index}@example.com",
            full_name="User",
            hashed_password="hash",
            role=UserRole.CUSTOMER,
        )
        warehouse = Warehouse(tenant_id=tenant.id, code="SAME", name="Warehouse", region="test")
        db.add_all([user, warehouse])
        db.flush()
        stock = Inventory(
            tenant_id=tenant.id, sku="SHARED", product_name="Snapshot", warehouse_id=warehouse.id, available_quantity=2
        )
        db.add(stock)
        stocks.append(stock)
        for attempt in range(4):
            order = Order(
                tenant_id=tenant.id,
                principal_scope=f"user:{user.id}",
                customer_id=user.id,
                idempotency_key=f"same-{attempt}",
                status=OrderStatus.CREATED,
            )
            order.items.append(OrderItem(sku="SHARED", product_name="Snapshot", quantity=1, unit_price=10))
            db.add(order)
            db.flush()
            events.append(
                DomainEvent(
                    event_type=EventNames.ORDER_CREATED, aggregate_id=order.id, tenant_id=tenant.id, payload={}
                ).model_dump(mode="json")
            )
    db.commit()
    monkeypatch.setattr(tasks, "SessionLocal", TestingSessionLocal)
    gate = Barrier(8)

    def process(event):
        gate.wait(timeout=30)
        tasks.process_event.run(event)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(process, events))
    db.expire_all()
    for tenant, stock in zip(tenants, stocks, strict=True):
        assert stock.available_quantity == 0 and stock.reserved_quantity == 2
        assert db.query(Order).filter_by(tenant_id=tenant.id, status=OrderStatus.INVENTORY_RESERVED).count() == 2
        assert db.query(Order).filter_by(tenant_id=tenant.id, status=OrderStatus.FAILED).count() == 2
        rows = (
            db.query(OutboxEvent)
            .filter_by(tenant_id=tenant.id)
            .filter(OutboxEvent.event_type != EventNames.ORDER_STATUS_CHANGED)
            .all()
        )
        assert len(rows) == 4 and all(row.payload["tenant_id"] == str(tenant.id) for row in rows)
        assert (
            db.query(OutboxEvent).filter_by(tenant_id=tenant.id, event_type=EventNames.ORDER_STATUS_CHANGED).count()
            == 4
        )
