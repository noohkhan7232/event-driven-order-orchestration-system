from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from structlog.contextvars import get_contextvars

from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.order_state import transition
from app.events.contracts import DomainEvent
from app.models.entities import AuditLog, Order, Shipment, ShipmentEvent, Tenant
from app.models.enums import OrderStatus, ShipmentStatus
from app.providers.registry import get_shipping_provider
from app.repositories.outbox import OutboxRepository


class ShippingService:
    def __init__(self, db: Session):
        self.db = db

    def create_shipment(self, order: Order) -> Shipment:
        tenant = self.db.get(Tenant, order.tenant_id)
        name = tenant.shipping_provider if tenant else get_settings().shipping_provider
        provider = get_shipping_provider() if name == get_settings().shipping_provider else get_shipping_provider(name)
        attempt = (
            self.db.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == order.id, AuditLog.action == "order_reopened")
            )
            or 0
        )
        result = provider.create_shipment(order, idempotency_key=f"{order.id}:shipment:{attempt}")
        shipment = Shipment(
            order=order,
            status=result.status,
            provider=provider.name,
            provider_reference=result.provider_reference,
            carrier="manual" if provider.name == "manual" else "internal_fulfillment",
            tracking_number=None if provider.name == "manual" else result.provider_reference,
            estimated_delivery_at=None if provider.name == "manual" else datetime.now(UTC) + timedelta(days=4),
        )
        self.db.add(shipment)
        self.db.flush()
        self.db.add(ShipmentEvent(shipment_id=shipment.id, status=ShipmentStatus.CREATED, notes="Shipment created"))
        self.db.add(AuditLog(entity_type="shipment", entity_id=shipment.id, action="shipment_created", payload={}))
        return shipment

    def record_event(self, shipment: Shipment, status: ShipmentStatus, location: str | None, notes: str | None) -> None:
        # Preserve every observation, but never move a terminal shipment backwards.
        rank = {
            ShipmentStatus.PENDING: 0,
            ShipmentStatus.CREATED: 1,
            ShipmentStatus.IN_TRANSIT: 2,
            ShipmentStatus.DELAYED: 2,
            ShipmentStatus.DELIVERED: 3,
            ShipmentStatus.CANCELLED: 4,
        }
        if (
            shipment.status not in {ShipmentStatus.DELIVERED, ShipmentStatus.CANCELLED}
            and rank[status] >= rank[shipment.status]
        ):
            shipment.status = status
        observation = ShipmentEvent(shipment_id=shipment.id, status=status, location=location, notes=notes)
        self.db.add(observation)
        self.db.flush()
        order = shipment.order
        OutboxRepository(self.db).add(
            DomainEvent(
                event_type="shipment.status_changed",
                correlation_id=get_contextvars().get("request_id"),
                aggregate_id=order.id,
                tenant_id=order.tenant_id,
                payload={"shipment_id": str(shipment.id), "shipment_event_id": str(observation.id)},
            ),
            "shipments",
        )
        self.db.add(
            AuditLog(entity_type="shipment", entity_id=shipment.id, action=f"shipment_{status.value}", payload={})
        )

    def cancel_for_order(self, order: Order) -> None:
        for shipment in order.shipments:
            if shipment.status != ShipmentStatus.CANCELLED:
                name = shipment.provider or get_settings().shipping_provider
                provider = (
                    get_shipping_provider(name)
                    if name in {"mock", "manual"} and name != get_settings().shipping_provider
                    else get_shipping_provider()
                )
                provider.cancel_shipment(order, shipment, idempotency_key=f"{order.id}:cancel:{shipment.id}")
                # The order lock decides cancellation vs its queued delivery
                # consumer. A permitted cancellation must cancel the shipment
                # even when a delivery observation has not been consumed yet.
                shipment.status = ShipmentStatus.CANCELLED
                self.record_event(shipment, ShipmentStatus.CANCELLED, None, "Order cancelled")


def apply_shipment_status(db: Session, order: Order, event: DomainEvent) -> None:
    from uuid import UUID

    logger = get_logger(__name__)
    try:
        identity = UUID(event.payload.get("shipment_event_id", ""))
    except (ValueError, TypeError, AttributeError):
        logger.info("shipment_event_skipped", reason="invalid_identity")
        return
    observation = db.scalar(
        select(ShipmentEvent)
        .join(Shipment)
        .where(
            ShipmentEvent.id == identity,
            Shipment.order_id == order.id,
        )
    )
    if (
        observation is None
        or observation.shipment.status == ShipmentStatus.CANCELLED
        or event.tenant_id != order.tenant_id
        or order.status in {OrderStatus.CANCELLED, OrderStatus.FAILED, OrderStatus.DELIVERED}
    ):
        logger.info("shipment_event_skipped", reason="stale_or_unowned")
        return
    if observation.status == ShipmentStatus.IN_TRANSIT and order.status == OrderStatus.FULFILLMENT_REQUESTED:
        transition(order, OrderStatus.SHIPPED, db=db, correlation_id=event.correlation_id)
    elif observation.status == ShipmentStatus.DELIVERED and order.status in {
        OrderStatus.FULFILLMENT_REQUESTED,
        OrderStatus.SHIPPED,
    }:
        if order.status == OrderStatus.FULFILLMENT_REQUESTED:
            transition(order, OrderStatus.SHIPPED, db=db, correlation_id=event.correlation_id)
        transition(order, OrderStatus.DELIVERED, db=db, correlation_id=event.correlation_id)
    else:
        logger.info("shipment_event_skipped", reason="non_transitioning_observation")
