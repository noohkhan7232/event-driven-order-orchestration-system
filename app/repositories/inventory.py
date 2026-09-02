from sqlalchemy import select

from app.models.entities import Inventory
from app.repositories.base import Repository


class InventoryRepository(Repository[Inventory]):
    model = Inventory

    def get_by_sku(self, sku: str) -> list[Inventory]:
        return list(
            self.db.scalars(
                select(Inventory)
                .where(Inventory.sku == sku)
                .order_by(Inventory.available_quantity.desc())
                .with_for_update()
            ).all()
        )
