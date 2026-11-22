import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.principal import Principal, aware, get_principal
from app.core.config import Settings, get_settings
from app.core.crypto import CredentialVault
from app.core.exceptions import ConflictError, NotFoundError
from app.core.ssrf import validate_target
from app.db.session import get_db
from app.models.entities import AuditLog, WebhookDelivery, WebhookEndpoint
from app.schemas.webhooks import DeliveryRead, DeliveryStatus, EndpointCreate, EndpointPatch, EndpointRead
from app.services.outbound_webhooks import queue_ping
from app.services.payment_timing import database_now

router = APIRouter()


def owned(db: Session, principal: Principal, identity: UUID, *, lock: bool = False) -> WebhookEndpoint:
    query = select(WebhookEndpoint).where(
        WebhookEndpoint.id == identity,
        WebhookEndpoint.tenant_id == principal.tenant_id,
        WebhookEndpoint.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None:
        raise NotFoundError("Webhook endpoint not found")
    principal.require_owner()
    return row


def audit(db: Session, principal: Principal, row: WebhookEndpoint, action: str) -> None:
    db.add(
        AuditLog(
            actor_id=principal.require_owner().id,
            entity_type="webhook_endpoint",
            entity_id=row.id,
            action=action,
            payload={},
        )
    )


@router.post("", status_code=201)
def create(
    payload: EndpointCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_settings),
):
    principal.require_owner()
    url = validate_target(payload.url, settings)
    secret = "whsec_" + secrets.token_urlsafe(32)
    row = WebhookEndpoint(
        tenant_id=principal.tenant_id,
        url=url,
        events=payload.events,
        description=payload.description,
        secret_enc=CredentialVault(settings.credentials_encryption_key).encrypt(SecretStr(secret)),
    )
    db.add(row)
    db.flush()
    audit(db, principal, row, "webhook_endpoint_created")
    db.commit()
    return {**EndpointRead.model_validate(row).model_dump(mode="json"), "signing_secret": secret}


@router.get("")
def listing(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    principal.require_owner()
    query = select(WebhookEndpoint).where(
        WebhookEndpoint.tenant_id == principal.tenant_id, WebhookEndpoint.deleted_at.is_(None)
    )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(WebhookEndpoint.created_at, WebhookEndpoint.id).offset(offset).limit(limit))
    return {"total": total, "items": [EndpointRead.model_validate(row) for row in rows]}


@router.patch("/{endpoint_id}", response_model=EndpointRead)
def patch(
    endpoint_id: UUID,
    payload: EndpointPatch,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_settings),
):
    owned(db, principal, endpoint_id)
    if payload.url is not None:
        validate_target(payload.url, settings)  # DNS before any row lock.
    row = owned(db, principal, endpoint_id, lock=True)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    if payload.is_active is not None:
        row.disabled_at = None if payload.is_active else database_now(db)
        row.disabled_reason = None if payload.is_active else "owner_disabled"
        if payload.is_active:
            row.consecutive_failures = 0
    audit(db, principal, row, "webhook_endpoint_updated")
    db.commit()
    return row


@router.delete("/{endpoint_id}", status_code=204)
def delete(endpoint_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    row = owned(db, principal, endpoint_id, lock=True)
    row.is_active = False
    row.deleted_at = row.disabled_at = database_now(db)
    row.disabled_reason = "owner_deleted"
    audit(db, principal, row, "webhook_endpoint_deleted")
    db.commit()
    return Response(status_code=204)


@router.post("/{endpoint_id}/rotate-secret")
def rotate(
    endpoint_id: UUID,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_settings),
):
    row = owned(db, principal, endpoint_id, lock=True)
    secret = "whsec_" + secrets.token_urlsafe(32)
    row.secret_enc = CredentialVault(settings.credentials_encryption_key).encrypt(SecretStr(secret))
    audit(db, principal, row, "webhook_secret_rotated")
    db.commit()
    return {"id": str(row.id), "signing_secret": secret}


@router.post("/{endpoint_id}/test", status_code=202, response_model=DeliveryRead)
def ping(endpoint_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    row = owned(db, principal, endpoint_id, lock=True)
    if not row.is_active:
        raise ConflictError("Webhook endpoint is inactive")
    delivery = queue_ping(db, row)
    audit(db, principal, row, "webhook_ping_queued")
    db.commit()
    return delivery


@router.get("/{endpoint_id}/deliveries")
def deliveries(
    endpoint_id: UUID,
    status: DeliveryStatus | None = None,
    event_type: str | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    owned(db, principal, endpoint_id)
    query = select(WebhookDelivery).where(
        WebhookDelivery.endpoint_id == endpoint_id, WebhookDelivery.tenant_id == principal.tenant_id
    )
    if status is not None:
        query = query.where(WebhookDelivery.status == status)
    if event_type is not None:
        query = query.where(WebhookDelivery.event_type == event_type)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id).offset(offset).limit(limit))
    return {"total": total, "items": [DeliveryRead.model_validate(row) for row in rows]}


@router.post("/{endpoint_id}/deliveries/{delivery_id}/replay", response_model=DeliveryRead)
def replay(
    endpoint_id: UUID, delivery_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
):
    endpoint = owned(db, principal, endpoint_id, lock=True)
    row = db.scalar(
        select(WebhookDelivery)
        .where(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.endpoint_id == endpoint.id,
            WebhookDelivery.tenant_id == principal.tenant_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFoundError("Webhook delivery not found")
    now = database_now(db)
    if not endpoint.is_active or (row.claim_token and row.lease_until and aware(row.lease_until) > now):
        raise ConflictError("Webhook delivery is inactive or in progress")
    row.status, row.attempts, row.next_attempt_at = "pending", 0, now
    row.claim_token = row.lease_until = row.delivered_at = None
    row.last_error = row.last_status_code = None
    row.replay_count += 1
    audit(db, principal, endpoint, "webhook_delivery_replayed")
    db.commit()
    return row
