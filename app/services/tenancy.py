import hashlib
import re
import secrets
import string
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.principal import Principal
from app.auth.security import hash_password
from app.core.exceptions import ConflictError, NotFoundError
from app.models.entities import ApiKey, Tenant, User
from app.models.enums import UserRole
from app.schemas.tenancy import ApiKeyCreate, TenantRegister


def register_tenant(db: Session, payload: TenantRegister) -> Tenant:
    stem = re.sub(r"[^a-z0-9]+", "-", payload.business_name.lower()).strip("-")[:29] or "store"
    slug = payload.slug or f"{stem}-{uuid4().hex[:8]}"
    tenant = Tenant(name=payload.business_name, slug=slug)
    try:
        db.add(tenant)
        db.flush()
        db.add(
            User(
                tenant_id=tenant.id,
                email=payload.owner_email.lower(),
                full_name=payload.owner_name,
                hashed_password=hash_password(payload.owner_password),
                role=UserRole.ADMIN,
            )
        )
        db.commit()
        return tenant
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Business slug or owner email is already registered") from exc


def create_api_key(db: Session, principal: Principal, payload: ApiKeyCreate) -> tuple[ApiKey, str]:
    owner = principal.require_owner()
    for _ in range(10):
        public = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
        key = f"oos_live_{public}_{secrets.token_urlsafe(32)}"
        row = ApiKey(
            tenant_id=principal.tenant_id,
            name=payload.name,
            key_prefix=key[:17],
            key_hash=hashlib.sha256(key[17:].encode()).hexdigest(),
            scopes=list(dict.fromkeys(payload.scopes)),
            expires_at=payload.expires_at,
            created_by=owner.id,
        )
        db.add(row)
        try:
            db.commit()
            return row, key
        except IntegrityError:
            db.rollback()
    raise ConflictError("Unable to allocate a unique API key prefix")


def revoke_api_key(db: Session, principal: Principal, key_id: UUID) -> None:
    row = db.scalar(select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == principal.tenant_id))
    if row is None:
        raise NotFoundError("API key not found")
    principal.require_owner()
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        db.commit()
