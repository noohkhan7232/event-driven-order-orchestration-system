from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.principal import Principal, get_principal
from app.core.config import Settings, get_settings
from app.core.exceptions import AuthorizationError, ConflictError, DomainError, NotFoundError
from app.db.session import get_db
from app.models.entities import AuditLog, Order, Shipment, Tenant
from app.schemas.orders import ShipmentRead
from app.schemas.shipments import ShipmentEventCreate, ShipmentUpdate, ShippingSettings
from app.services.shipping import ShippingService

router = APIRouter()
settings_router = APIRouter()


def owned_shipment(db: Session, principal: Principal, shipment_id: UUID) -> Shipment:
    order = db.scalar(
        select(Order)
        .join(Shipment)
        .where(Shipment.id == shipment_id, Order.tenant_id == principal.tenant_id)
        .with_for_update(of=Order)
    )
    if order is None:
        raise NotFoundError("Shipment not found")
    principal.require("orders:write")
    if principal.is_customer:
        raise AuthorizationError("Staff or integration credentials required")
    shipment = db.scalar(
        select(Shipment).where(Shipment.id == shipment_id).with_for_update().execution_options(populate_existing=True)
    )
    if shipment is None:
        raise NotFoundError("Shipment not found")
    return shipment


@router.patch("/{shipment_id}", response_model=ShipmentRead)
def update_shipment(
    shipment_id: UUID,
    payload: ShipmentUpdate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    shipment = owned_shipment(db, principal, shipment_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "carrier" and value is None:
            raise DomainError("Carrier cannot be null")
        setattr(shipment, key, value)
    db.add(
        AuditLog(
            actor_id=principal.user.id if principal.user else None,
            entity_type="shipment",
            entity_id=shipment.id,
            action="shipment_tracking_updated",
            payload={},
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ConflictError("Tracking number is already assigned") from None
    return shipment


@settings_router.get("")
def read_shipping(db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    principal.require_owner()
    tenant = db.get(Tenant, principal.tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found")
    return {"shipping_provider": tenant.shipping_provider}


@settings_router.patch("")
def update_shipping(
    payload: ShippingSettings,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_settings),
):
    owner = principal.require_owner()
    if payload.shipping_provider == "mock" and settings.environment not in {"local", "development", "test"}:
        raise DomainError("Mock shipping is forbidden outside local/development/test")
    tenant = db.get(Tenant, principal.tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found")
    tenant.shipping_provider = payload.shipping_provider
    db.add(
        AuditLog(
            actor_id=owner.id,
            entity_type="tenant",
            entity_id=tenant.id,
            action="shipping_provider_updated",
            payload={"shipping_provider": tenant.shipping_provider},
        )
    )
    db.commit()
    return {"shipping_provider": tenant.shipping_provider}


@router.post("/{shipment_id}/events")
def record_shipment_event(
    shipment_id: UUID,
    payload: ShipmentEventCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    shipment = owned_shipment(db, principal, shipment_id)
    ShippingService(db).record_event(shipment, payload.status, payload.location, payload.notes)
    db.commit()
    return {"status": "accepted", "shipment_id": str(shipment_id)}
