from uuid import UUID

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
from app.models.entities import AuditLog, FailedJob, Inventory, Order, ProcessedEvent, Tenant
from app.models.enums import JobStatus, OrderStatus, PaymentStatus
from app.notifications.service import queue_notifications, run_notifications
from app.observability.metrics import DUPLICATE_EVENTS
from app.providers.contracts import ProviderDeclined
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.services.failed_jobs import record_failure
from app.services.inventory import InventoryService
from app.services.outbound_webhooks import fanout
from app.services.payment_expiry import expire_orders
from app.services.payment_reconciliation import reconcile_payments
from app.services.payment_sessions import handle_payment
from app.services.payments import PaymentService
from app.services.provider_events import UnmatchedPayment, process_provider_event
from app.services.refunds import RefundBusy, record_refund_failure, run_refund
from app.services.shipping import ShippingService, apply_shipment_status
from app.services.webhook_delivery import run_delivery_batch
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


class ResilientTask(Task):
    autoretry_for = (TransientError, OperationalError, ConnectionError)
    dont_autoretry_for = (BusinessError,)
    retry_kwargs: dict[str, int | None] = {"max_retries": get_settings().task_max_retries}
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


class RefundTask(ResilientTask):
    # Lease waits do not consume the durable provider-attempt budget.
    max_retries = None
    retry_kwargs = {"max_retries": None}

    def on_failure(self, exc: Exception, task_id: str, args: tuple, kwargs: dict, einfo: object) -> None:
        try:
            identity = UUID(args[0] if args else kwargs["refund_id"])
            with SessionLocal() as db:
                record_refund_failure(db, identity)
        except Exception:
            logger.error("refund_failure_store_unavailable", task_id=task_id)


@celery_app.task(bind=True, base=RefundTask, name="app.workers.tasks.process_refund")
def process_refund(self, refund_id: str) -> None:
    try:
        identity = UUID(refund_id)
    except (ValueError, TypeError):
        raise BusinessError("Invalid refund identity") from None
    try:
        run_refund(SessionLocal, identity)
    except RefundBusy as busy:
        raise self.retry(countdown=busy.seconds) from None


class EventTask(ResilientTask):
    # Explicit unmatched-notice retries are bounded by receipt age. Ordinary
    # technical failures retain the finite autoretry budget in retry_kwargs.
    max_retries = None


@celery_app.task(bind=True, base=EventTask, name="app.workers.tasks.process_event")
def process_event(self, raw_event: dict) -> None:
    try:
        event = DomainEvent.model_validate(raw_event)
    except Exception as exc:
        logger.exception("unexpected_error", original_event=raw_event)
        raise TransientError("Invalid domain event") from exc
    with SessionLocal() as db:
        try:
            if event.event_type == "payment.provider_event":
                process_provider_event(db, event)
                return
            # Global lock ordering: ORDER first, INVENTORY (SKU, warehouse_id) second.
            order = OrderRepository(db).get_with_details(event.aggregate_id, for_update=True)
            if not order:
                logger.warning("order_missing_for_event", order_id=str(event.aggregate_id))
                db.rollback()
                return
            if event.event_type in {EventNames.PAYMENT_REQUESTED, EventNames.INVENTORY_RESERVED}:
                handle_payment(db, order, event, self.request.retries)
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
            if event.event_type == "shipment.status_changed":
                apply_shipment_status(db, order, event)
            if (
                event.event_type == EventNames.ORDER_STATUS_CHANGED
                and order.payment_method == "cod"
                and order.status == OrderStatus.DELIVERED
                and event.payload.get("to") == "delivered"
            ):
                for payment in order.payments:
                    if payment.provider == "cod" and payment.status == PaymentStatus.INITIATED:
                        payment.status = PaymentStatus.CAPTURED
                        payment.provider_status = "collected"
                        db.add(
                            AuditLog(entity_type="payment", entity_id=payment.id, action="cod_collected", payload={})
                        )
            # order.status_changed is informational. Its consumer may record the
            # marker, never transition again (which would create an event loop).
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
            fanout(db, order, event)
            queue_notifications(db, order, event)
            db.commit()
        except UnmatchedPayment as exc:
            db.rollback()
            raise self.retry(exc=exc, countdown=exc.retry_after, max_retries=None) from None
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
    trace = event.correlation_id if event else None
    try:
        InventoryService(db).reserve_for_order(order)
    except InsufficientInventoryError:
        transition(order, OrderStatus.FAILED, db=db, reason="INSUFFICIENT_INVENTORY", correlation_id=trace)
        _emit(db, order, EventNames.INVENTORY_FAILED, "inventory", event)
        return
    transition(order, OrderStatus.INVENTORY_RESERVED, db=db, correlation_id=trace)
    _emit(db, order, EventNames.PAYMENT_REQUESTED, "payments", event)


def _authorize_payment(db: Session, order: Order, event: DomainEvent | None = None) -> None:
    trace = event.correlation_id if event else None
    transition(order, OrderStatus.PAYMENT_PENDING, db=db, correlation_id=trace)
    payment = PaymentService(db).authorize(order, retry_attempt=process_event.request.retries)
    if payment.status == PaymentStatus.AUTHORIZED:
        transition(order, OrderStatus.PAID, db=db, correlation_id=trace)
        _emit(db, order, EventNames.PAYMENT_AUTHORIZED, "shipments", event)
    else:
        InventoryService(db).release_for_order(order)
        transition(order, OrderStatus.FAILED, db=db, reason="PAYMENT_DECLINED", correlation_id=trace)
        _emit(db, order, EventNames.PAYMENT_FAILED, "payments", event)


def _create_shipment(db: Session, order: Order, event: DomainEvent | None = None) -> None:
    trace = event.correlation_id if event else None
    try:
        ShippingService(db).create_shipment(order)
    except ProviderDeclined:
        PaymentService(db).refund(order, reason="SHIPMENT_FAILED", correlation_id=trace)
        InventoryService(db).release_for_order(order)
        transition(order, OrderStatus.FAILED, db=db, reason="SHIPMENT_FAILED", correlation_id=trace)
        _emit(db, order, "shipment.failed", "shipments", event)
        return
    transition(order, OrderStatus.FULFILLMENT_REQUESTED, db=db, correlation_id=trace)
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


@celery_app.task(name="app.workers.tasks.expire_unpaid_orders")
def expire_unpaid_orders() -> int:
    return expire_orders(SessionLocal)


@celery_app.task(name="app.workers.tasks.reconcile_pending_payments")
def reconcile_pending_payments() -> dict[str, int]:
    return reconcile_payments(SessionLocal)


@celery_app.task(name="app.workers.tasks.deliver_webhooks")
def deliver_webhooks() -> dict[str, int]:
    return run_delivery_batch(SessionLocal, get_settings())


@celery_app.task(name="app.workers.tasks.send_notifications")
def send_notifications() -> dict[str, int]:
    return run_notifications(SessionLocal, get_settings())
