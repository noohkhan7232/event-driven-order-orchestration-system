from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth.principal import Principal, get_principal
from app.core.exceptions import AuthorizationError, NotFoundError
from app.db.session import get_db
from app.models.entities import AuditLog, Tenant, User
from app.schemas.common import Page
from app.schemas.tenancy import TenantRead

router = APIRouter()


def platform_admin(principal: Principal = Depends(get_principal)) -> User:
    if not principal.is_platform_admin or principal.user is None:
        raise AuthorizationError("Platform administrator JWT required")
    return principal.user


def tenant(db: Session, tenant_id: UUID, *, lock: bool = False) -> Tenant:
    statement = select(Tenant).where(Tenant.id == tenant_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(statement)
    if row is None:
        raise NotFoundError("Tenant not found")
    return row


@router.get("", response_model=Page[TenantRead])
def listing(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Literal["active", "suspended"] | None = None,
    q: str | None = Query(None, max_length=160),
    actor: User = Depends(platform_admin),
    db: Session = Depends(get_db),
):
    conditions = [Tenant.status == status] if status else []
    if q and q.strip():
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append(or_(Tenant.name.ilike(pattern, escape="\\"), Tenant.slug.ilike(pattern, escape="\\")))
    total = db.scalar(select(func.count()).select_from(Tenant).where(*conditions)) or 0
    rows = list(
        db.scalars(select(Tenant).where(*conditions).order_by(Tenant.created_at, Tenant.id).limit(limit).offset(offset))
    )
    return Page(items=rows, total=total, limit=limit, offset=offset)


@router.get("/{tenant_id}", response_model=TenantRead)
def get(tenant_id: UUID, actor: User = Depends(platform_admin), db: Session = Depends(get_db)):
    return tenant(db, tenant_id)


def set_status(db: Session, tenant_id: UUID, actor: User, status: str) -> Tenant:
    row = tenant(db, tenant_id, lock=True)
    if row.status != status:
        row.status = status
        db.add(
            AuditLog(
                actor_id=actor.id,
                entity_type="tenant",
                entity_id=row.id,
                action="tenant_suspended" if status == "suspended" else "tenant_activated",
                payload={},
            )
        )
    db.commit()
    return row


@router.post("/{tenant_id}/suspend", response_model=TenantRead)
def suspend(tenant_id: UUID, actor: User = Depends(platform_admin), db: Session = Depends(get_db)):
    return set_status(db, tenant_id, actor, "suspended")


@router.post("/{tenant_id}/activate", response_model=TenantRead)
def activate(tenant_id: UUID, actor: User = Depends(platform_admin), db: Session = Depends(get_db)):
    return set_status(db, tenant_id, actor, "active")
