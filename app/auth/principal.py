"""Resolve one credential into explicit, database-authoritative tenant identity."""

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.core.exceptions import AuthorizationError, DomainError
from app.db.session import get_db
from app.models.entities import ApiKey, Tenant, User
from app.models.enums import UserRole

SCOPES = frozenset(
    {"orders:read", "orders:write", "catalog:read", "catalog:write", "inventory:read", "inventory:write"}
)
KEY_PATTERN = re.compile(r"oos_live_[A-Za-z0-9]{8}_[A-Za-z0-9_-]{32,}\Z")


class AuthenticationError(DomainError):
    status_code = 401
    code = "unauthenticated"


class AmbiguousCredentials(DomainError):
    code = "ambiguous_credentials"


class TenantSuspended(AuthorizationError):
    code = "tenant_suspended"


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class Principal:
    tenant_id: UUID | None
    user: User | None = None
    api_key: ApiKey | None = None

    @property
    def is_platform_admin(self) -> bool:
        return bool(self.user and self.user.is_platform_admin)

    @property
    def is_customer(self) -> bool:
        return bool(self.user and self.user.role == UserRole.CUSTOMER)

    def allows(self, scope: str) -> bool:
        if self.tenant_id is None:
            return False
        if self.api_key:
            return scope in self.api_key.scopes
        if self.user:
            return scope in (
                SCOPES if self.user.role in {UserRole.ADMIN, UserRole.VENDOR} else {"orders:read", "orders:write"}
            )
        return False

    def require(self, scope: str) -> None:
        if not self.allows(scope):
            raise AuthorizationError("Insufficient scope")

    def require_owner(self) -> User:
        if not self.user or self.user.role != UserRole.ADMIN or self.tenant_id is None:
            raise AuthorizationError("Tenant owner JWT required")
        return self.user


def require_active_tenant(db: Session, tenant_id: UUID | None) -> None:
    if tenant_id is None:
        return
    tenant = db.get(Tenant, tenant_id, populate_existing=True)
    if tenant is None:
        raise AuthenticationError("Invalid credentials")
    if tenant.status == "suspended":
        raise TenantSuspended("Tenant access is suspended")


def authenticate_jwt(token: str, db: Session) -> Principal:
    try:
        payload = decode_access_token(token)
        user_id = UUID(payload["sub"])
    except (ValueError, TypeError, KeyError) as exc:
        raise AuthenticationError("Invalid credentials") from exc
    user = db.get(User, user_id, populate_existing=True)
    if user is None or not user.is_active:
        raise AuthenticationError("Invalid credentials")
    # tid is informational: ownership is loaded from the user row on every request.
    require_active_tenant(db, user.tenant_id)
    return Principal(tenant_id=user.tenant_id, user=user)


def authenticate_api_key(key: str, db: Session) -> Principal:
    row = db.scalar(select(ApiKey).where(ApiKey.key_prefix == key[:12]).execution_options(populate_existing=True))
    candidate = hashlib.sha256(key[12:].encode()).hexdigest()
    matches = hmac.compare_digest(row.key_hash if row else "0" * 64, candidate)
    now = datetime.now(UTC)
    # Comparison always occurs, including malformed/unknown/revoked/expired keys.
    if not (
        KEY_PATTERN.fullmatch(key)
        and row
        and matches
        and row.revoked_at is None
        and (row.expires_at is None or aware(row.expires_at) > now)
    ):
        raise AuthenticationError("Invalid credentials")
    require_active_tenant(db, row.tenant_id)
    if row.last_used_at is None or aware(row.last_used_at) <= now - timedelta(minutes=5):
        db.execute(
            update(ApiKey)
            .where(
                ApiKey.id == row.id,
                or_(ApiKey.last_used_at.is_(None), ApiKey.last_used_at <= now - timedelta(minutes=5)),
            )
            .values(last_used_at=now)
            .execution_options(synchronize_session="fetch")
        )
        db.commit()
        db.refresh(row)
    return Principal(tenant_id=row.tenant_id, api_key=row)


def get_principal(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Principal:
    if authorization is not None and x_api_key is not None:
        raise AmbiguousCredentials("Supply exactly one credential")
    if x_api_key is not None:
        return authenticate_api_key(x_api_key, db)
    if authorization is not None:
        scheme, separator, token = authorization.partition(" ")
        if separator and scheme.lower() == "bearer" and token:
            return authenticate_jwt(token, db)
    raise AuthenticationError("Invalid credentials")
