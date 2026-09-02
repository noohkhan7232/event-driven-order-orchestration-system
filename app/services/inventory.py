from sqlalchemy.orm import Session

from app.core.exceptions import InsufficientInventoryError, NotFoundError
from app.models.entities import AuditLog, Inventory, Order
from app.repositories.inventory import InventoryRepository


class InventoryService:
    def __init__(self, db: Session):
        self.db = db
        self.inventory = InventoryRepository(db)

    def reserve_for_order(self, order: Order) -> None:
        for item in order.items:
            locations = self.inventory.get_by_sku(item.sku)
            selected = next((row for row in locations if row.available_quantity >= item.quantity), None)
            if not selected:
                raise InsufficientInventoryError("Insufficient inventory", {"sku": item.sku, "quantity": item.quantity})
            selected.available_quantity -= item.quantity
            selected.reserved_quantity += item.quantity
            item.product_name = selected.product_name
            item.warehouse_id = selected.warehouse_id
        self.db.add(AuditLog(entity_type="order", entity_id=order.id, action="inventory_reserved", payload={}))

    def release_for_order(self, order: Order) -> None:
        for item in order.items:
            if not item.warehouse_id:
                continue
            rows = self.inventory.get_by_sku(item.sku)
            selected = next((row for row in rows if row.warehouse_id == item.warehouse_id), None)
            if selected:
                selected.available_quantity += item.quantity
                selected.reserved_quantity = max(0, selected.reserved_quantity - item.quantity)
        self.db.add(AuditLog(entity_type="order", entity_id=order.id, action="inventory_released", payload={}))

    def low_stock_items(self) -> list[Inventory]:
        rows, _ = self.inventory.list(limit=100, offset=0)
        return [row for row in rows if row.available_quantity <= row.low_stock_threshold]

    def get_or_404(self, inventory_id):
        item = self.inventory.get(inventory_id)
        if not item:
            raise NotFoundError("Inventory item not found")
        return item
