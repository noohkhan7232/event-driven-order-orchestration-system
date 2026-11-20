from uuid import UUID

from sqlalchemy import func, select

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

    def list_for_tenant(
        self, tenant_id: UUID, limit: int, offset: int, *, low_stock: bool = False
    ) -> tuple[list[Inventory], int]:
        conditions = [Inventory.tenant_id == tenant_id]
        if low_stock:
            conditions.append(Inventory.available_quantity <= Inventory.low_stock_threshold)
        total = self.db.scalar(select(func.count()).select_from(Inventory).where(*conditions)) or 0
        rows = list(
            self.db.scalars(
                select(Inventory).where(*conditions).order_by(Inventory.sku, Inventory.id).limit(limit).offset(offset)
            )
        )
        return rows, total
