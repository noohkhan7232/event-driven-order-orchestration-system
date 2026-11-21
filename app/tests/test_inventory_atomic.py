import pytest
from test_consumers import consume

from app.core.exceptions import InsufficientInventoryError
from app.events.contracts import EventNames
from app.models.entities import AuditLog, OrderItem, OutboxEvent, Payment, Shipment, ShipmentEvent
from app.models.enums import OrderStatus, PaymentStatus, ShipmentStatus
from app.services.inventory import InventoryService
from app.services.orders import OrderService


def test_partial_inventory_reservation_is_never_left_behind(db, workflow):
    order, stock = workflow
    order.items.append(OrderItem(sku="ZZ-MISSING", product_name="Missing", quantity=1, unit_price=10))
    db.commit()
    with pytest.raises(InsufficientInventoryError):
        InventoryService(db).reserve_for_order(order)
    assert stock.available_quantity == 10
    assert stock.reserved_quantity == 0
    db.rollback()
    consume(order, EventNames.ORDER_CREATED)
    assert order.status == OrderStatus.FAILED and order.failure_reason == "INSUFFICIENT_INVENTORY"
    assert stock.available_quantity == 10 and not order.inventory_reserved
    assert (
        db.query(OutboxEvent).filter(OutboxEvent.event_type != EventNames.ORDER_STATUS_CHANGED).one().event_type
        == "inventory.failed"
    )
    assert (
        db.query(OutboxEvent).filter_by(event_type=EventNames.ORDER_STATUS_CHANGED).one().payload["payload"]["to"]
        == "failed"
    )
    assert (
        db.query(OutboxEvent).filter_by(event_type=EventNames.ORDER_STATUS_CHANGED).one().payload["payload"]["reason"]
        == "INSUFFICIENT_INVENTORY"
    )


def test_fulfillment_cancel_compensates_shipment_payment_inventory_atomically(db, workflow):
    order, stock = workflow
    for kind in [EventNames.ORDER_CREATED, EventNames.INVENTORY_RESERVED, EventNames.PAYMENT_AUTHORIZED]:
        consume(order, kind)
    OrderService(db).cancel_order(order.id, order.customer)
    shipment = db.query(Shipment).one()
    assert shipment.status == ShipmentStatus.CANCELLED
    assert db.query(ShipmentEvent).filter_by(status=ShipmentStatus.CANCELLED).count() == 1
    assert db.query(AuditLog).filter_by(action="shipment_cancelled").count() == 1
    assert db.query(Payment).one().status == PaymentStatus.REFUNDED
    assert stock.available_quantity == 10 and stock.reserved_quantity == 0
    assert db.query(OutboxEvent).filter_by(event_type="order.cancelled").count() == 1
    OrderService(db).cancel_order(order.id, order.customer)
    assert db.query(OutboxEvent).filter_by(event_type="order.cancelled").count() == 1


def test_failed_cancel_rolls_back_every_compensation(db, workflow, monkeypatch):
    from app.repositories.outbox import OutboxRepository

    order, stock = workflow
    for kind in [EventNames.ORDER_CREATED, EventNames.INVENTORY_RESERVED, EventNames.PAYMENT_AUTHORIZED]:
        consume(order, kind)
    monkeypatch.setattr(OutboxRepository, "add", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("cancel outbox")))
    with pytest.raises(RuntimeError, match="cancel outbox"):
        OrderService(db).cancel_order(order.id, order.customer)
    db.expire_all()
    assert order.status == OrderStatus.FULFILLMENT_REQUESTED
    assert stock.available_quantity == 9 and stock.reserved_quantity == 1
    assert db.query(Payment).one().status == PaymentStatus.AUTHORIZED
    assert db.query(Shipment).one().status == ShipmentStatus.CREATED
    assert db.query(AuditLog).filter_by(action="payment_refunded").count() == 0
