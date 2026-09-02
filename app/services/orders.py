from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.events.contracts import DomainEvent, EventNames
from app.events.publisher import EventPublisher
from app.models.entities import AuditLog, Order, OrderItem, User
from app.models.enums import OrderStatus, UserRole
from app.repositories.orders import OrderRepository
from app.schemas.orders import OrderCreate


class OrderService:
    def __init__(self, db: Session):
        self.db = db
        self.orders = OrderRepository(db)
        self.publisher = EventPublisher()

    def create_order(self, customer: User, payload: OrderCreate, idempotency_key: str) -> Order:
        existing = self.orders.get_by_idempotency_key(idempotency_key)
        if existing:
            return existing

        # Demo pricing is intentionally deterministic; real pricing would call catalog/pricing services.
        items = [
            OrderItem(sku=item.sku, product_name="Pending catalog lookup", quantity=item.quantity, unit_price=Decimal("49.99"))
            for item in payload.items
        ]
        total = sum((item.unit_price * item.quantity for item in items), Decimal("0.00"))
        order = Order(
            customer_id=customer.id,
            status=OrderStatus.CREATED,
            idempotency_key=idempotency_key,
            currency=payload.currency.upper(),
            total_amount=total,
            items=items,
        )
        self.orders.add(order)
        self.db.add(AuditLog(actor_id=customer.id, entity_type="order", entity_id=order.id, action="order_created", payload={}))
        self.db.commit()
        self.publisher.publish(
            DomainEvent(event_type=EventNames.ORDER_CREATED, aggregate_id=order.id, payload={"order_id": str(order.id)}),
            queue="orders",
        )
        return self.orders.get_with_details(order.id) or order

    def get_order(self, order_id: UUID, user: User) -> Order:
        order = self.orders.get_with_details(order_id)
        if not order:
            raise NotFoundError("Order not found")
        if user.role == UserRole.CUSTOMER and order.customer_id != user.id:
            raise NotFoundError("Order not found")
        return order

    def cancel_order(self, order_id: UUID, user: User, reason: str | None = None) -> Order:
        order = self.get_order(order_id, user)
        if order.status in {OrderStatus.SHIPPED, OrderStatus.DELIVERED}:
            raise ConflictError("Shipped or delivered orders cannot be cancelled")
        order.status = OrderStatus.CANCELLED
        order.failure_reason = reason
        self.db.add(AuditLog(actor_id=user.id, entity_type="order", entity_id=order.id, action="order_cancelled", payload={}))
        self.db.commit()
        self.publisher.publish(
            DomainEvent(event_type="order.cancelled", aggregate_id=order.id, payload={"order_id": str(order.id)}),
            queue="inventory",
        )
        return self.orders.get_with_details(order.id) or order
