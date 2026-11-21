from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.principal import Principal, get_principal
from app.core.exceptions import AuthorizationError, NotFoundError
from app.db.session import get_db
from app.models.entities import Order, Shipment
from app.schemas.shipments import ShipmentEventCreate
from app.services.shipping import ShippingService

router = APIRouter()


@router.post("/{shipment_id}/events")
def record_shipment_event(
    shipment_id: UUID,
    payload: ShipmentEventCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    shipment = db.scalar(
        select(Shipment).join(Order).where(Shipment.id == shipment_id, Order.tenant_id == principal.tenant_id)
    )
    if not shipment:
        raise NotFoundError("Shipment not found")
    principal.require("orders:write")
    if principal.is_customer:
        raise AuthorizationError("Staff or integration credentials required")
    ShippingService(db).record_event(shipment, payload.status, payload.location, payload.notes)
    db.commit()
    return {"status": "accepted", "shipment_id": str(shipment_id)}
