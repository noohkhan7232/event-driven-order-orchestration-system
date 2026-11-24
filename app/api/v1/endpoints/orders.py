from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.principal import Principal, aware, get_principal
from app.core.exceptions import AuthorizationError
from app.db.session import get_db
from app.models.entities import AuditLog, OrderTimeline, Refund
from app.models.enums import OrderStatus
from app.repositories.orders import OrderRepository
from app.schemas.common import Page
from app.schemas.orders import (
    ActivityEvent,
    OrderActivity,
    OrderCreate,
    OrderRead,
    PaymentActivity,
    RefundActivity,
    ShipmentActivity,
)
from app.services.orders import OrderService

router = APIRouter()


@router.get("/{order_id}/activity", response_model=OrderActivity)
def order_activity(order_id: UUID, db: Session = Depends(get_db), user: Principal = Depends(get_principal)):
    order = OrderService(db).get_order(order_id, user)
    if user.is_customer:
        raise AuthorizationError("Staff or integration credentials required")
    timeline = db.scalars(
        select(OrderTimeline)
        .where(OrderTimeline.order_id == order.id, OrderTimeline.tenant_id == user.tenant_id)
        .order_by(OrderTimeline.id)
    )
    refunds = db.scalars(
        select(Refund)
        .where(Refund.order_id == order.id, Refund.tenant_id == user.tenant_id)
        .order_by(Refund.created_at, Refund.id)
    )
    return OrderActivity(
        timeline=[ActivityEvent(id=str(row.id), at=row.created_at, status=row.to_status) for row in timeline],
        payments=[
            PaymentActivity(
                id=str(row.id), at=row.updated_at, status=row.status.value, provider_status=row.provider_status
            )
            for row in order.payments
        ],
        shipments=[
            ShipmentActivity(id=str(event.id), shipment_id=row.id, at=event.created_at, status=event.status.value)
            for row in order.shipments
            for event in sorted(row.events, key=lambda e: (e.created_at, e.id))
        ],
        refunds=[
            RefundActivity(
                id=str(row.id), at=row.updated_at, status=row.status, amount=row.amount, currency=row.currency
            )
            for row in refunds
        ],
    )


@router.post("/{order_id}/tracking/rotate")
def rotate_tracking(order_id: UUID, db: Session = Depends(get_db), user: Principal = Depends(get_principal)):
    order = OrderService(db).get_order(order_id, user, for_update=True, scope="orders:write")
    if user.is_customer:
        raise AuthorizationError("Staff or integration credentials required")
    order.tracking_version += 1
    db.add(
        AuditLog(
            actor_id=user.user.id if user.user else None,
            entity_type="order",
            entity_id=order.id,
            action="tracking_rotated",
            payload={},
        )
    )
    db.commit()
    return {"tracking_token": order.tracking_token, "tracking_url": order.tracking_url}


@router.post("", response_model=OrderRead, status_code=status.HTTP_202_ACCEPTED)
def create_order(
    payload: OrderCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: Principal = Depends(get_principal),
):
    return OrderService(db).create_order(user, payload, idempotency_key)


@router.get("/{order_id}", response_model=OrderRead)
def get_order(order_id: UUID, db: Session = Depends(get_db), user: Principal = Depends(get_principal)):
    return OrderService(db).get_order(order_id, user)


@router.get("", response_model=Page[OrderRead])
def list_orders(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    order_status: OrderStatus | None = Query(default=None, alias="status"),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    external_reference: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
    user: Principal = Depends(get_principal),
):
    repo = OrderRepository(db)
    user.require("orders:read")
    if user.tenant_id is None:
        from app.core.exceptions import AuthorizationError

        raise AuthorizationError("Tenant principal required")
    items, total = repo.list_for_tenant(
        user.tenant_id,
        limit,
        offset,
        user.user.id if user.is_customer and user.user else None,
        status=order_status,
        created_from=aware(created_from) if created_from else None,
        created_to=aware(created_to) if created_to else None,
        external_reference=external_reference,
    )
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/{order_id}/cancel", response_model=OrderRead)
def cancel_order(
    order_id: UUID,
    reason: str | None = None,
    db: Session = Depends(get_db),
    user: Principal = Depends(get_principal),
):
    return OrderService(db).cancel_order(order_id, user, reason)
