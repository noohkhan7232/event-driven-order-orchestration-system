from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session, sessionmaker

from app.auth.principal import Principal, get_principal
from app.core.config import Settings, get_settings
from app.core.exceptions import AuthorizationError
from app.core.ratelimit import RateLimitError, client_ip
from app.db.session import get_db
from app.services.orders import OrderService
from app.services.sse import StreamResponse, StreamScope, TimelineReader, acquire_stream
from app.services.tracking import PUBLIC_HEADERS, tracked_order

router = APIRouter()
public_router = APIRouter()
STREAM_SCHEMA: dict[int | str, dict[str, Any]] = {
    200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}
}
MAX_CURSOR = 9223372036854775807


def response(request: Request, db: Session, scope: StreamScope, settings: Settings, cursor: int | None):
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    db.rollback()
    try:
        reader = TimelineReader(factory, scope, settings, cursor)
        lease = acquire_stream(scope.tenant_id, client_ip(request, settings.trusted_proxy_count), settings)
    except RateLimitError as exc:
        exc.headers.update(PUBLIC_HEADERS)
        raise
    return StreamResponse(reader, lease)


@router.get("/stream", response_class=StreamResponse, responses=STREAM_SCHEMA)
def stream_orders(
    request: Request,
    order_id: UUID | None = None,
    last_event_id: int | None = Header(None, ge=0, le=MAX_CURSOR),
    cursor: int | None = Query(None, alias="last_event_id", ge=0, le=MAX_CURSOR),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_settings),
):
    if principal.tenant_id is None:
        raise AuthorizationError("Tenant principal required")
    if order_id:
        OrderService(db).get_order(order_id, principal)
    principal.require("orders:read")
    scope = StreamScope(
        principal.tenant_id,
        order_id,
        customer_id=principal.user.id if principal.is_customer and principal.user else None,
        user_id=principal.user.id if principal.user else None,
        key_id=principal.api_key.id if principal.api_key else None,
    )
    return response(request, db, scope, settings, last_event_id if last_event_id is not None else cursor)


@public_router.get("/{token}/stream", response_class=StreamResponse, responses=STREAM_SCHEMA)
def stream_tracking(
    token: str,
    request: Request,
    last_event_id: int | None = Header(None, ge=0, le=MAX_CURSOR),
    cursor: int | None = Query(None, alias="last_event_id", ge=0, le=MAX_CURSOR),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    order, tenant = tracked_order(db, token, settings)
    scope = StreamScope(tenant.id, order.id, tracking_version=order.tracking_version)
    return response(request, db, scope, settings, last_event_id if last_event_id is not None else cursor)
