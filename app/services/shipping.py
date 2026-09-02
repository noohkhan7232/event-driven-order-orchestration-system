from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.entities import AuditLog, Order, Shipment, ShipmentEvent
from app.models.enums import ShipmentStatus


class ShippingService:
    def __init__(self, db: Session):
        self.db = db

    def create_shipment(self, order: Order) -> Shipment:
        shipment = Shipment(
            order_id=order.id,
            status=ShipmentStatus.CREATED,
            tracking_number=f"ARC{uuid4().hex[:12].upper()}",
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
        self.db.add(AuditLog(entity_type="shipment", entity_id=shipment.id, action=f"shipment_{status.value}", payload={}))
