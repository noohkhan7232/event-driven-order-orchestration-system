from contextlib import nullcontext

import pytest

from app.core.exceptions import ConflictError
from app.events.contracts import DomainEvent, EventNames
from app.models.entities import AuditLog, Inventory, Order, OrderItem, Payment, User, Warehouse
from app.models.enums import OrderStatus, PaymentStatus, UserRole
from app.services.inventory import InventoryService
from app.services.orders import OrderService
from app.workers import tasks


@pytest.fixture
def order_stock(db, monkeypatch):
    published = []
    monkeypatch.setattr("app.events.publisher.celery_app.send_task", lambda *a, **kw: published.append(kw))
    user = User(email="cancel@example.com", hashed_password="hash", full_name="Cancel User", role=UserRole.CUSTOMER)
    warehouse = Warehouse(code="WH", name="Warehouse", region="test")
    db.add_all([user, warehouse])
    db.flush()
    stock = Inventory(sku="SKU", product_name="Product", warehouse_id=warehouse.id, available_quantity=10)
    order = Order(customer_id=user.id, idempotency_key="cancel", status=OrderStatus.CREATED)
    order.items.append(OrderItem(sku="SKU", quantity=2, unit_price=10, product_name="Product"))
    db.add_all([stock, order])
    db.commit()
    return order, stock, user, published


def test_cancel_before_reserve_publishes_nothing(db, order_stock):
    order, stock, user, published = order_stock
    OrderService(db).cancel_order(order.id, user)
    assert stock.available_quantity == 10
    assert stock.reserved_quantity == 0
    assert published == []


def test_cancel_after_reserve_restores_once_and_double_cancel_is_noop(db, order_stock):
    order, stock, user, published = order_stock
    InventoryService(db).reserve_for_order(order)
    order.status = OrderStatus.INVENTORY_RESERVED
    db.commit()
    assert stock.available_quantity == 8
    OrderService(db).cancel_order(order.id, user)
    assert stock.available_quantity == 10
    assert stock.reserved_quantity == 0
    count = db.query(AuditLog).count()
    OrderService(db).cancel_order(order.id, user)
    InventoryService(db).release_for_order(order)
    db.commit()
    assert stock.available_quantity == 10
    assert not order.inventory_reserved
    assert db.query(AuditLog).count() == count
    assert published == []


def test_cancel_refunds_authorized_payment_once(db, order_stock):
    order, stock, user, published = order_stock
    payment = Payment(order_id=order.id, amount=20, status=PaymentStatus.AUTHORIZED)
    db.add(payment)
    order.status = OrderStatus.PAID
    db.commit()
    OrderService(db).cancel_order(order.id, user)
    assert payment.status == PaymentStatus.REFUNDED
    OrderService(db).cancel_order(order.id, user)
    assert db.query(AuditLog).filter_by(action="payment_refunded").count() == 1


@pytest.mark.parametrize("status", [OrderStatus.FAILED, OrderStatus.SHIPPED, OrderStatus.DELIVERED])
def test_cancel_terminal_order_conflicts(db, order_stock, status):
    order, stock, user, published = order_stock
    order.status = status
    db.commit()
    with pytest.raises(ConflictError):
        OrderService(db).cancel_order(order.id, user)
    assert published == []


@pytest.mark.parametrize(
    "event_type", [EventNames.ORDER_CREATED, EventNames.INVENTORY_RESERVED, EventNames.PAYMENT_AUTHORIZED]
)
def test_worker_skips_cancelled_order(db, order_stock, monkeypatch, event_type):
    order, stock, user, published = order_stock
    order.status = OrderStatus.CANCELLED
    db.commit()
    monkeypatch.setattr(tasks, "SessionLocal", lambda: nullcontext(db))
    steps = []
    for name in ["_reserve_inventory", "_authorize_payment", "_create_shipment"]:
        monkeypatch.setattr(tasks, name, lambda *a: steps.append(True))
    tasks.process_event.run(
        DomainEvent(event_type=event_type, aggregate_id=order.id, payload={}).model_dump(mode="json")
    )
    assert steps == []
    assert order.status == OrderStatus.CANCELLED


def test_payment_failure_clears_inventory_reservation(db, order_stock, monkeypatch):
    order, stock, user, published = order_stock
    InventoryService(db).reserve_for_order(order)
    db.commit()
    monkeypatch.setattr(tasks.PaymentService, "authorize", lambda *a: Payment(status=PaymentStatus.FAILED))
    tasks._authorize_payment(db, order)
    assert stock.available_quantity == 10
    assert stock.reserved_quantity == 0
    assert order.status == OrderStatus.FAILED
    assert not order.inventory_reserved
