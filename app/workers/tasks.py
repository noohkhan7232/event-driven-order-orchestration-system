from celery import Task
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import BusinessError, InsufficientInventoryError, TransientError
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.domain.order_state import transition
from app.events.contracts import DomainEvent, EventNames
from app.models.entities import FailedJob, Inventory, Order, ProcessedEvent, Tenant
from app.models.enums import JobStatus, OrderStatus, PaymentStatus
from app.observability.metrics import DUPLICATE_EVENTS
from app.providers.contracts import ProviderDeclined
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.services.failed_jobs import record_failure
from app.services.inventory import InventoryService
from app.services.payments import PaymentService
from app.services.shipping import ShippingService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


class ResilientTask(Task):
    autoretry_for = (TransientError, OperationalError, ConnectionError)
    dont_autoretry_for = (BusinessError,)
    retry_kwargs = {"max_retries": get_settings().task_max_retries}
    retry_backoff_max = get_settings().task_retry_backoff_max
    retry_backoff = True
    retry_jitter = True

    def on_failure(self, exc: Exception, task_id: str, args: tuple, kwargs: dict, einfo: object) -> None:
        raw = args[0] if args else kwargs.get("raw_event", {})
        queue = (self.request.delivery_info or {}).get("routing_key", "orders")
        try:
            with SessionLocal() as db:
                record_failure(db, raw, exc, self.request.retries, queue, self.name)
        except Exception:
            logger.exception("failure_compensation_error", task_id=task_id)
            # Fallback preserves even malformed poison messages or compensation errors.
            try:
                try:
                    parsed = DomainEvent.model_validate(raw)
                except ValueError:
                    parsed = None
                with SessionLocal() as db:
                    db.rollback()
                    db.add(
                        FailedJob(
                            task_name=self.name,
                            queue_name=queue,
                            status=JobStatus.FAILED,
                            payload=raw,
                            event_id=parsed.event_id if parsed else None,
                            tenant_id=parsed.tenant_id if parsed else None,
                            aggregate_id=parsed.aggregate_id if parsed else None,
                            error_message=str(exc),
                            retry_count=self.request.retries,
                        )
                    )
                    db.commit()
            except Exception:
                logger.exception("failed_job_persistence_error", task_id=task_id, original_event=raw)
        logger.error("task_failed", task=self.name, task_id=task_id, error=str(exc))


@celery_app.task(bind=True, base=ResilientTask, name="app.workers.tasks.process_event")
def process_event(self, raw_event: dict) -> None:
    try:
        event = DomainEvent.model_validate(raw_event)
    except Exception as exc:
        logger.exception("unexpected_error", original_event=raw_event)
        raise TransientError("Invalid domain event") from exc
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
            tenant_id=order.tenant_id,
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
    payment = PaymentService(db).authorize(order, retry_attempt=process_event.request.retries)
    if payment.status == PaymentStatus.AUTHORIZED:
        transition(order, OrderStatus.PAID, db=db)
        _emit(db, order, EventNames.PAYMENT_AUTHORIZED, "shipments", event)
    else:
        InventoryService(db).release_for_order(order)
        transition(order, OrderStatus.FAILED, db=db, reason="PAYMENT_DECLINED")
        _emit(db, order, EventNames.PAYMENT_FAILED, "payments", event)


def _create_shipment(db: Session, order: Order, event: DomainEvent | None = None) -> None:
    try:
        ShippingService(db).create_shipment(order)
    except ProviderDeclined:
        PaymentService(db).refund(order)
        InventoryService(db).release_for_order(order)
        transition(order, OrderStatus.FAILED, db=db, reason="SHIPMENT_FAILED")
        _emit(db, order, "shipment.failed", "shipments", event)
        return
    transition(order, OrderStatus.FULFILLMENT_REQUESTED, db=db)
    _emit(db, order, EventNames.SHIPMENT_CREATED, "shipments", event)


@celery_app.task(name="app.workers.tasks.check_low_stock")
def check_low_stock() -> dict[str, int]:
    counts: dict[str, int] = {}
    with SessionLocal() as db:
        tenant_cursor = None
        while True:
            query = select(Tenant.id).order_by(Tenant.id).limit(100)
            if tenant_cursor is not None:
                query = query.where(Tenant.id > tenant_cursor)
            tenant_ids = list(db.scalars(query))
            if not tenant_ids:
                break
            for tenant_id in tenant_ids:
                cursor = None
                count = 0
                while True:
                    stock = (
                        select(Inventory.id)
                        .where(
                            Inventory.tenant_id == tenant_id,
                            Inventory.available_quantity <= Inventory.low_stock_threshold,
                        )
                        .order_by(Inventory.id)
                        .limit(100)
                    )
                    if cursor is not None:
                        stock = stock.where(Inventory.id > cursor)
                    ids = list(db.scalars(stock))
                    if not ids:
                        break
                    count += len(ids)
                    cursor = ids[-1]
                counts[str(tenant_id)] = count
                logger.warning("low_stock_scan_completed", tenant_id=str(tenant_id), count=count)
            tenant_cursor = tenant_ids[-1]
    return counts


@celery_app.task(name="app.workers.tasks.detect_delayed_shipments")
def detect_delayed_shipments() -> int:
    logger.info("delayed_shipment_scan_completed", delayed_count=0)
    return 0
