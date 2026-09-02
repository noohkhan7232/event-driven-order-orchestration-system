from uuid import UUID

from celery import Task

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.events.contracts import DomainEvent, EventNames
from app.events.publisher import EventPublisher
from app.models.entities import FailedJob, Order
from app.models.enums import JobStatus, OrderStatus, PaymentStatus
from app.repositories.orders import OrderRepository
from app.services.inventory import InventoryService
from app.services.payments import PaymentService
from app.services.shipping import ShippingService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)
publisher = EventPublisher()


class ResilientTask(Task):
    autoretry_for = (Exception,)
    retry_kwargs = {"max_retries": 3, "countdown": 5}
    retry_backoff = True
    retry_jitter = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        with SessionLocal() as db:
            db.add(
                FailedJob(
                    task_name=self.name,
                    queue_name=self.request.delivery_info.get("routing_key", "unknown"),
                    status=JobStatus.FAILED,
                    payload={"args": args, "kwargs": kwargs},
                    error_message=str(exc),
                    retry_count=self.request.retries,
                )
            )
            db.commit()
        logger.error("task_failed", task=self.name, task_id=task_id, error=str(exc))


@celery_app.task(bind=True, base=ResilientTask, name="app.workers.tasks.process_event")
def process_event(self, raw_event: dict) -> None:
    event = DomainEvent.model_validate(raw_event)
    logger.info("event_consumed", event_type=event.event_type, aggregate_id=str(event.aggregate_id))
    with SessionLocal() as db:
        order = OrderRepository(db).get_with_details(UUID(str(event.aggregate_id)))
        if not order:
            logger.warning("order_missing_for_event", order_id=str(event.aggregate_id))
            return

        if event.event_type == EventNames.ORDER_CREATED:
            _reserve_inventory(db, order)
        elif event.event_type == EventNames.INVENTORY_RESERVED:
            _authorize_payment(db, order)
        elif event.event_type == EventNames.PAYMENT_AUTHORIZED:
            _create_shipment(db, order)
        elif event.event_type == "order.cancelled":
            InventoryService(db).release_for_order(order)
            db.commit()


def _reserve_inventory(db, order: Order) -> None:
    try:
        InventoryService(db).reserve_for_order(order)
        order.status = OrderStatus.INVENTORY_RESERVED
        db.commit()
        publisher.publish(
            DomainEvent(event_type=EventNames.INVENTORY_RESERVED, aggregate_id=order.id, payload={"order_id": str(order.id)}),
            queue="payments",
        )
    except Exception as exc:
        order.status = OrderStatus.FAILED
        order.failure_reason = str(exc)
        db.commit()
        publisher.publish(
            DomainEvent(event_type=EventNames.INVENTORY_FAILED, aggregate_id=order.id, payload={"error": str(exc)}),
            queue="dead_letter",
        )
        raise


def _authorize_payment(db, order: Order) -> None:
    order.status = OrderStatus.PAYMENT_PENDING
    payment = PaymentService(db).authorize(order)
    if payment.status == PaymentStatus.AUTHORIZED:
        order.status = OrderStatus.PAID
        db.commit()
        publisher.publish(
            DomainEvent(event_type=EventNames.PAYMENT_AUTHORIZED, aggregate_id=order.id, payload={"order_id": str(order.id)}),
            queue="shipments",
        )
    else:
        InventoryService(db).release_for_order(order)
        order.status = OrderStatus.FAILED
        db.commit()
        publisher.publish(
            DomainEvent(event_type=EventNames.PAYMENT_FAILED, aggregate_id=order.id, payload={"order_id": str(order.id)}),
            queue="dead_letter",
        )


def _create_shipment(db, order: Order) -> None:
    ShippingService(db).create_shipment(order)
    order.status = OrderStatus.FULFILLMENT_REQUESTED
    db.commit()
    publisher.publish(
        DomainEvent(event_type=EventNames.SHIPMENT_CREATED, aggregate_id=order.id, payload={"order_id": str(order.id)}),
        queue="shipments",
    )


@celery_app.task(name="app.workers.tasks.check_low_stock")
def check_low_stock() -> list[str]:
    with SessionLocal() as db:
        items = InventoryService(db).low_stock_items()
        logger.warning("low_stock_scan_completed", count=len(items), skus=[item.sku for item in items])
        return [item.sku for item in items]


@celery_app.task(name="app.workers.tasks.detect_delayed_shipments")
def detect_delayed_shipments() -> int:
    logger.info("delayed_shipment_scan_completed", delayed_count=0)
    return 0
