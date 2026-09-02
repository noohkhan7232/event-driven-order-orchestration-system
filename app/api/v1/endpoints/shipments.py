from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import require_roles
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.entities import Shipment, User
from app.models.enums import UserRole
from app.schemas.shipments import ShipmentEventCreate
from app.services.shipping import ShippingService

router = APIRouter()


@router.post("/{shipment_id}/events")
def record_shipment_event(
    shipment_id: UUID,
    payload: ShipmentEventCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.VENDOR)),
):
    shipment = db.get(Shipment, shipment_id)
    if not shipment:
        raise NotFoundError("Shipment not found")
    ShippingService(db).record_event(shipment, payload.status, payload.location, payload.notes)
    db.commit()
    return {"status": "accepted", "shipment_id": str(shipment_id)}
