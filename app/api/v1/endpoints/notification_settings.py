from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.auth.principal import Principal, get_principal
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.entities import AuditLog, Tenant

router = APIRouter()


class NotificationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    notifications_enabled: bool


def tenant_setting(db: Session, principal: Principal) -> Tenant:
    principal.require_owner()
    tenant = db.get(Tenant, principal.tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found")
    return tenant


@router.get("", response_model=NotificationSettings)
def read(db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    return tenant_setting(db, principal)


@router.patch("", response_model=NotificationSettings)
def patch(payload: NotificationSettings, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    tenant = tenant_setting(db, principal)
    tenant.notifications_enabled = payload.notifications_enabled
    db.add(
        AuditLog(
            actor_id=principal.require_owner().id,
            entity_type="tenant",
            entity_id=tenant.id,
            action="notifications_setting_updated",
            payload={"enabled": tenant.notifications_enabled},
        )
    )
    db.commit()
    return tenant
