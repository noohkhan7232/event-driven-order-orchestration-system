import hashlib
import json
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from structlog.contextvars import get_contextvars

from app.auth.principal import Principal
from app.core.exceptions import AuthorizationError, ConflictError, IdempotencyConflictError, NotFoundError
from app.domain.order_state import transition
from app.events.contracts import DomainEvent, EventNames
from app.models.entities import AuditLog, Order, OrderItem, User
from app.models.enums import OrderStatus
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.schemas.orders import OrderCreate, OrderItemCreate
from app.services.inventory import InventoryService
from app.services.payments import PaymentService
from app.services.shipping import ShippingService


class OrderService:
    def __init__(self, db: Session):
        self.db = db
        self.orders = OrderRepository(db)
        self.outbox = OutboxRepository(db)

    def create_order(self, customer: User | Principal, payload: OrderCreate, idempotency_key: str) -> Order:
        principal = customer if isinstance(customer, Principal) else Principal(customer.tenant_id, user=customer)
        principal.require("orders:write")
        if principal.tenant_id is None:
            raise AuthorizationError("A tenant principal is required")
        tenant_id = principal.tenant_id
        customer_id = principal.user.id if principal.user else None
        principal_scope = f"user:{customer_id}" if customer_id else "tenant"
        request_hash = self._request_hash(payload)
        existing = self.orders.get_by_idempotency_key(tenant_id, principal_scope, idempotency_key)
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
            customer_id=customer_id,
            tenant_id=tenant_id,
            principal_scope=principal_scope,
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
                    tenant_id=order.tenant_id,
                    payload={"order_id": str(order.id)},
                    correlation_id=get_contextvars().get("request_id"),
                ),
                "orders",
            )
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self.orders.get_by_idempotency_key(tenant_id, principal_scope, idempotency_key)
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

    def get_order(
        self, order_id: UUID, user: User | Principal, *, for_update: bool = False, scope: str = "orders:read"
    ) -> Order:
        principal = user if isinstance(user, Principal) else Principal(user.tenant_id, user=user)
        if principal.tenant_id is None:
            raise NotFoundError("Order not found")
        order = self.orders.get_with_details(order_id, tenant_id=principal.tenant_id, for_update=for_update)
        if not order or (principal.is_customer and principal.user and order.customer_id != principal.user.id):
            raise NotFoundError("Order not found")
        principal.require(scope)
        return order

    def cancel_order(self, order_id: UUID, user: User | Principal, reason: str | None = None) -> Order:
        principal = user if isinstance(user, Principal) else Principal(user.tenant_id, user=user)
        actor_id = principal.user.id if principal.user else None
        try:
            order = self.get_order(order_id, principal, for_update=True, scope="orders:write")
            if order.status == OrderStatus.CANCELLED:
                self.db.commit()
                return order
            if order.status in {OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.FAILED}:
                raise ConflictError("Terminal orders cannot be cancelled")
            InventoryService(self.db).release_for_order(order)
            PaymentService(self.db).refund(order)
            ShippingService(self.db).cancel_for_order(order)
            transition(order, OrderStatus.CANCELLED, db=self.db, reason=reason, actor=actor_id)
            self.db.add(
                AuditLog(
                    actor_id=actor_id, entity_type="order", entity_id=order.id, action="order_cancelled", payload={}
                )
            )
            self.outbox.add(
                DomainEvent(
                    event_type="order.cancelled",
                    aggregate_id=order.id,
                    tenant_id=order.tenant_id,
                    payload={"order_id": str(order.id)},
                    correlation_id=get_contextvars().get("request_id"),
                ),
                "orders",
            )
            self.db.commit()
            return self.orders.get_with_details(order.id) or order
        except Exception:
            self.db.rollback()
            raise
