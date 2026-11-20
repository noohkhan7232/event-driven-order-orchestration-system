import hashlib
import json
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from structlog.contextvars import get_contextvars

from app.core.exceptions import ConflictError, IdempotencyConflictError, NotFoundError
from app.domain.order_state import transition
from app.events.contracts import DomainEvent, EventNames
from app.models.entities import AuditLog, Order, OrderItem, User
from app.models.enums import OrderStatus, UserRole
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.schemas.orders import OrderCreate, OrderItemCreate
from app.services.inventory import InventoryService
from app.services.payments import PaymentService


class OrderService:
    def __init__(self, db: Session):
        self.db = db
        self.orders = OrderRepository(db)
        self.outbox = OutboxRepository(db)

    def create_order(self, customer: User, payload: OrderCreate, idempotency_key: str) -> Order:
        request_hash = self._request_hash(payload)
        customer_id = customer.id
        existing = self.orders.get_by_idempotency_key(customer_id, idempotency_key)
        if existing:
            return self._check_replay(existing, request_hash)

        # Demo pricing is intentionally deterministic; real pricing would call catalog/pricing services.
        items = [
            OrderItem(
                sku=item.sku, product_name="Pending catalog lookup", quantity=item.quantity, unit_price=Decimal("49.99")
            )
            for item in payload.items
        ]
        total = sum((item.unit_price * item.quantity for item in items), Decimal("0.00"))
        order = Order(
            customer_id=customer.id,
            status=OrderStatus.CREATED,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            currency=payload.currency.upper(),
            total_amount=total,
            items=items,
        )
        try:
            self.orders.add(order)
            self.db.add(
                AuditLog(
                    actor_id=customer_id, entity_type="order", entity_id=order.id, action="order_created", payload={}
                )
            )
            self.outbox.add(
                DomainEvent(
                    event_type=EventNames.ORDER_CREATED,
                    aggregate_id=order.id,
                    payload={"order_id": str(order.id)},
                    correlation_id=get_contextvars().get("request_id"),
                ),
                "orders",
            )
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self.orders.get_by_idempotency_key(customer_id, idempotency_key)
            if existing is None:
                raise
            return self._check_replay(existing, request_hash)
        except Exception:
            self.db.rollback()
            raise
        return self.orders.get_with_details(order.id) or order

    @staticmethod
    def _request_hash(payload: OrderCreate) -> str:
        canonical = payload.model_dump()
        canonical["currency"] = payload.currency.upper()
        canonical["items"] = sorted(canonical["items"], key=lambda item: (item["sku"], item["quantity"]))
        return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _check_replay(self, order: Order, request_hash: str) -> Order:
        # Legacy rows predate hashes; reconstruct only the validated request fields.
        original_hash = order.request_hash or self._request_hash(
            OrderCreate(
                currency=order.currency,
                items=[OrderItemCreate(sku=item.sku, quantity=item.quantity) for item in order.items],
            )
        )
        if original_hash != request_hash:
            raise IdempotencyConflictError("This idempotency key was already used with a different payload")
        return order

    def get_order(self, order_id: UUID, user: User) -> Order:
        order = self.orders.get_with_details(order_id)
        if not order:
            raise NotFoundError("Order not found")
        if user.role == UserRole.CUSTOMER and order.customer_id != user.id:
            raise NotFoundError("Order not found")
        return order

    def cancel_order(self, order_id: UUID, user: User, reason: str | None = None) -> Order:
        order = self.get_order(order_id, user)
        if order.status == OrderStatus.CANCELLED:
            return order
        if order.status in {OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.FAILED}:
            raise ConflictError("Terminal orders cannot be cancelled")
        InventoryService(self.db).release_for_order(order)
        PaymentService(self.db).refund(order)
        transition(order, OrderStatus.CANCELLED, db=self.db)
        order.failure_reason = reason
        self.db.add(
            AuditLog(actor_id=user.id, entity_type="order", entity_id=order.id, action="order_cancelled", payload={})
        )
        self.db.commit()
        return self.orders.get_with_details(order.id) or order
