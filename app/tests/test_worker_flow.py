from app.models.entities import Inventory, Order, OrderItem, User, Warehouse
from app.models.enums import OrderStatus, UserRole
from app.services.inventory import InventoryService


def test_inventory_reservation_deducts_available_and_records_warehouse(db):
    user = User(email="worker@example.com", hashed_password="hash", full_name="Worker", role=UserRole.CUSTOMER)
    warehouse = Warehouse(code="PHX-01", name="Phoenix Warehouse", region="west")
    db.add_all([user, warehouse])
    db.flush()
    db.add(Inventory(sku="SKU-RESERVE", product_name="Reserve Me", warehouse_id=warehouse.id, available_quantity=5))
    order = Order(customer_id=user.id, status=OrderStatus.CREATED, idempotency_key="worker-1", total_amount=99)
    order.items.append(OrderItem(sku="SKU-RESERVE", product_name="Pending", quantity=3, unit_price=33))
    db.add(order)
    db.commit()

    InventoryService(db).reserve_for_order(order)
    db.commit()

    inventory = db.query(Inventory).filter_by(sku="SKU-RESERVE").one()
    assert inventory.available_quantity == 2
    assert inventory.reserved_quantity == 3
    assert order.items[0].warehouse_id == warehouse.id
