from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import AuditLog, Order, Shipment, ShipmentEvent
from app.models.enums import ShipmentStatus
from app.providers.registry import get_shipping_provider


class ShippingService:
    def __init__(self, db: Session):
        self.db = db

    def create_shipment(self, order: Order) -> Shipment:
        provider = get_shipping_provider()
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
            order_id=order.id,
            status=result.status,
            provider=provider.name,
            provider_reference=result.provider_reference,
            tracking_number=result.provider_reference,
            estimated_delivery_at=datetime.now(UTC) + timedelta(days=4),
        )
        self.db.add(shipment)
        self.db.flush()
        self.db.add(ShipmentEvent(shipment_id=shipment.id, status=ShipmentStatus.CREATED, notes="Shipment created"))
        self.db.add(AuditLog(entity_type="shipment", entity_id=shipment.id, action="shipment_created", payload={}))
        return shipment

    def record_event(self, shipment: Shipment, status: ShipmentStatus, location: str | None, notes: str | None) -> None:
        shipment.status = status
        self.db.add(ShipmentEvent(shipment_id=shipment.id, status=status, location=location, notes=notes))
        self.db.add(
            AuditLog(entity_type="shipment", entity_id=shipment.id, action=f"shipment_{status.value}", payload={})
        )

    def cancel_for_order(self, order: Order) -> None:
        for shipment in order.shipments:
            if shipment.status != ShipmentStatus.CANCELLED:
                get_shipping_provider().cancel_shipment(
                    order, shipment, idempotency_key=f"{order.id}:cancel:{shipment.id}"
                )
                self.record_event(shipment, ShipmentStatus.CANCELLED, None, "Order cancelled")
