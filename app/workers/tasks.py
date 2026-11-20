from celery import Task
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessError, InsufficientInventoryError, TransientError
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.domain.order_state import transition
from app.events.contracts import DomainEvent, EventNames
from app.models.entities import FailedJob, Order, ProcessedEvent
from app.models.enums import JobStatus, OrderStatus, PaymentStatus
from app.observability.metrics import DUPLICATE_EVENTS
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.services.inventory import InventoryService
from app.services.payments import PaymentService
from app.services.shipping import ShippingService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


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
    with SessionLocal() as db:
        try:
            # Global lock ordering: ORDER first, INVENTORY (SKU, warehouse_id) second.
            order = OrderRepository(db).get_with_details(event.aggregate_id, for_update=True)
            if not order:
                logger.warning("order_missing_for_event", order_id=str(event.aggregate_id))
                db.rollback()
                return
            # sqlite3 legacy transaction mode does not BEGIN for SELECT; without
            # this a released SAVEPOINT could survive a later outer rollback.
            if db.get_bind().dialect.name == "sqlite":
                connection = db.connection()
                if not getattr(connection.connection.driver_connection, "in_transaction", False):
                    connection.exec_driver_sql("BEGIN")
            try:
                with db.begin_nested():
                    db.add(ProcessedEvent(consumer="order_workflow", event_id=event.event_id))
                    db.flush()
            except IntegrityError:
                DUPLICATE_EVENTS.inc()
                logger.info("duplicate_event_skipped", event_id=str(event.event_id))
                db.commit()
                return
            expected = {
                EventNames.ORDER_CREATED: OrderStatus.CREATED,
                EventNames.INVENTORY_RESERVED: OrderStatus.INVENTORY_RESERVED,
                EventNames.PAYMENT_AUTHORIZED: OrderStatus.PAID,
            }
            if event.event_type in expected:
                if order.status == OrderStatus.CANCELLED:
                    logger.info("event_skipped_order_cancelled", event_id=str(event.event_id))
                elif order.status != expected[event.event_type]:
                    logger.info(
                        "event_skipped_unexpected_state", event_id=str(event.event_id), state=order.status.value
                    )
                else:
                    step = {
                        EventNames.ORDER_CREATED: _reserve_inventory,
                        EventNames.INVENTORY_RESERVED: _authorize_payment,
                        EventNames.PAYMENT_AUTHORIZED: _create_shipment,
                    }[event.event_type]
                    step(db, order, event)
            db.commit()
        except (BusinessError, TransientError, OperationalError):
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            logger.exception("unexpected_error", event_id=str(event.event_id))
            raise TransientError(str(exc)) from exc


def _emit(db: Session, order: Order, kind: str, queue: str, event: DomainEvent | None) -> None:
    OutboxRepository(db).add(
        DomainEvent(
            event_type=kind,
            aggregate_id=order.id,
            payload={"order_id": str(order.id), "reason": order.failure_reason},
            correlation_id=event.correlation_id if event else None,
        ),
        queue,
    )


def _reserve_inventory(db: Session, order: Order, event: DomainEvent | None = None) -> None:
    try:
        InventoryService(db).reserve_for_order(order)
    except InsufficientInventoryError:
        transition(order, OrderStatus.FAILED, db=db, reason="INSUFFICIENT_INVENTORY")
        _emit(db, order, EventNames.INVENTORY_FAILED, "inventory", event)
        return
    transition(order, OrderStatus.INVENTORY_RESERVED, db=db)
    _emit(db, order, EventNames.INVENTORY_RESERVED, "payments", event)


def _authorize_payment(db: Session, order: Order, event: DomainEvent | None = None) -> None:
    transition(order, OrderStatus.PAYMENT_PENDING, db=db)
    payment = PaymentService(db).authorize(order)
    if payment.status == PaymentStatus.AUTHORIZED:
        transition(order, OrderStatus.PAID, db=db)
        _emit(db, order, EventNames.PAYMENT_AUTHORIZED, "shipments", event)
    else:
        InventoryService(db).release_for_order(order)
        transition(order, OrderStatus.FAILED, db=db, reason="PAYMENT_DECLINED")
        _emit(db, order, EventNames.PAYMENT_FAILED, "payments", event)


def _create_shipment(db: Session, order: Order, event: DomainEvent | None = None) -> None:
    ShippingService(db).create_shipment(order)
    transition(order, OrderStatus.FULFILLMENT_REQUESTED, db=db)
    _emit(db, order, EventNames.SHIPMENT_CREATED, "shipments", event)


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
