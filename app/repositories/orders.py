from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.entities import Order
from app.models.enums import OrderStatus
from app.repositories.base import Repository


class OrderRepository(Repository[Order]):
    model = Order

    def get_with_details(
        self, order_id: UUID, *, tenant_id: UUID | None = None, for_update: bool = False
    ) -> Order | None:
        statement = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items), selectinload(Order.payments), selectinload(Order.shipments))
        )
        if tenant_id is not None:
            statement = statement.where(Order.tenant_id == tenant_id)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.db.scalar(statement)

    def get_by_idempotency_key(self, tenant_id: UUID, principal_scope: str, key: str) -> Order | None:
        return self.db.scalar(
            select(Order)
            .where(Order.tenant_id == tenant_id, Order.principal_scope == principal_scope, Order.idempotency_key == key)
            .options(selectinload(Order.items), selectinload(Order.payments), selectinload(Order.shipments))
        )

    def list_for_customer(self, customer_id: UUID, limit: int, offset: int) -> list[Order]:
        return list(
            self.db.scalars(
                select(Order)
                .where(Order.customer_id == customer_id)
                .order_by(Order.created_at.desc())
                .limit(limit)
                .offset(offset)
                .options(selectinload(Order.items), selectinload(Order.payments), selectinload(Order.shipments))
            ).all()
        )

    def count_for_customer(self, customer_id: UUID) -> int:
        return self.db.scalar(select(func.count()).select_from(Order).where(Order.customer_id == customer_id)) or 0

    def list_for_tenant(
        self,
        tenant_id: UUID,
        limit: int,
        offset: int,
        customer_id: UUID | None = None,
        *,
        status: OrderStatus | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        external_reference: str | None = None,
    ) -> tuple[list[Order], int]:
        conditions = [Order.tenant_id == tenant_id]
        if customer_id is not None:
            conditions.append(Order.customer_id == customer_id)
        if status is not None:
            conditions.append(Order.status == status)
        if created_from is not None:
            conditions.append(Order.created_at >= created_from)
        if created_to is not None:
            conditions.append(Order.created_at <= created_to)
        if external_reference is not None:
            conditions.append(Order.external_reference == external_reference)
        statement = select(Order).where(*conditions)
        total = self.db.scalar(select(func.count()).select_from(Order).where(*conditions)) or 0
        items = list(
            self.db.scalars(
                statement.order_by(Order.created_at.desc())
                .limit(limit)
                .offset(offset)
                .options(selectinload(Order.items), selectinload(Order.payments), selectinload(Order.shipments))
            )
        )
        return items, total
