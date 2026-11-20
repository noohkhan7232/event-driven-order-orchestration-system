from uuid import UUID

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

    def lock_skus(self, tenant_id: UUID, skus: set[str]) -> list[Inventory]:
        return list(
            self.db.scalars(
                select(Inventory)
                .where(Inventory.tenant_id == tenant_id, Inventory.sku.in_(skus))
                .order_by(Inventory.sku, Inventory.warehouse_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
