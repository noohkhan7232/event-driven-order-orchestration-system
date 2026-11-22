"""Expire one locked order per transaction, with a bounded keyset scan."""

from sqlalchemy import select

from app.core.config import get_settings
from app.domain.order_state import transition
from app.events.contracts import DomainEvent
from app.models.entities import Order
from app.models.enums import OrderStatus
from app.repositories.outbox import OutboxRepository
from app.services.inventory import InventoryService
from app.services.payment_timing import capture_cutoff, database_now
from app.services.payments import PaymentService
from app.services.refunds import SessionFactory


def expire_orders(factory: SessionFactory) -> int:
    cursor = None
    count = 0
    for _ in range(get_settings().payment_maintenance_batch_size):
        with factory() as db:
            try:
                query = (
                    select(Order)
                    .where(
                        Order.status == OrderStatus.PAYMENT_PENDING,
                        Order.payment_method == "prepaid",
                        Order.payment_expires_at <= capture_cutoff(database_now(db)),
                    )
                    .order_by(Order.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
                if cursor is not None:
                    query = query.where(Order.id > cursor)
                order = db.scalar(query)
                if order is None:
                    db.rollback()
                    break
                cursor = order.id
                InventoryService(db).release_for_order(order)
                PaymentService(db).refund(order, reason="PAYMENT_EXPIRED")
                transition(order, OrderStatus.FAILED, db=db, reason="PAYMENT_EXPIRED")
                OutboxRepository(db).add(
                    DomainEvent(
                        event_type="payment.expired",
                        aggregate_id=order.id,
                        tenant_id=order.tenant_id,
                        payload={"order_id": str(order.id), "reason": "PAYMENT_EXPIRED"},
                    ),
                    "payments",
                )
                db.commit()
                count += 1
            except Exception:
                db.rollback()
                raise
    return count
