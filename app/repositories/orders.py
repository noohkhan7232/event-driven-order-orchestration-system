from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.entities import Order
from app.repositories.base import Repository


class OrderRepository(Repository[Order]):
    model = Order

    def get_with_details(self, order_id: UUID) -> Order | None:
        return self.db.scalar(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items), selectinload(Order.payments), selectinload(Order.shipments))
        )

    def get_by_idempotency_key(self, key: str) -> Order | None:
        return self.db.scalar(
            select(Order)
            .where(Order.idempotency_key == key)
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
