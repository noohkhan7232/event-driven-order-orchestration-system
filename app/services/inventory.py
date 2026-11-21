from sqlalchemy.orm import Session

from app.core.exceptions import InsufficientInventoryError
from app.models.entities import AuditLog, Order
from app.repositories.inventory import InventoryRepository


class InventoryService:
    def __init__(self, db: Session):
        self.db = db
        self.inventory = InventoryRepository(db)

    def reserve_for_order(self, order: Order) -> None:
        rows = self.inventory.lock_skus(order.tenant_id, {item.sku for item in order.items})
        remaining = {row.id: row.available_quantity for row in rows}
        allocations = []
        for item in sorted(order.items, key=lambda item: item.sku):
            selected = next((row for row in rows if row.sku == item.sku and remaining[row.id] >= item.quantity), None)
            if selected is None:
                raise InsufficientInventoryError("Insufficient inventory", {"sku": item.sku, "quantity": item.quantity})
            remaining[selected.id] -= item.quantity
            allocations.append((item, selected))
        # Plan every allocation before changing any stock or item warehouse assignment.
        for item, selected in allocations:
            selected.available_quantity -= item.quantity
            selected.reserved_quantity += item.quantity
            item.warehouse_id = selected.warehouse_id
        order.inventory_reserved = True
        self.db.add(AuditLog(entity_type="order", entity_id=order.id, action="inventory_reserved", payload={}))

    def release_for_order(self, order: Order) -> None:
        if not order.inventory_reserved:
            return
        rows = self.inventory.lock_skus(order.tenant_id, {item.sku for item in order.items})
        for item in sorted(order.items, key=lambda item: item.sku):
            if not item.warehouse_id:
                continue
            selected = next(
                (row for row in rows if row.sku == item.sku and row.warehouse_id == item.warehouse_id), None
            )
            if selected:
                selected.available_quantity += item.quantity
                selected.reserved_quantity = max(0, selected.reserved_quantity - item.quantity)
        order.inventory_reserved = False
        self.db.add(AuditLog(entity_type="order", entity_id=order.id, action="inventory_released", payload={}))
