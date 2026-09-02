from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_roles
from app.db.session import get_db
from app.models.entities import User
from app.models.enums import UserRole
from app.repositories.orders import OrderRepository
from app.schemas.common import Page
from app.schemas.orders import OrderCreate, OrderRead
from app.services.orders import OrderService

router = APIRouter()


@router.post("", response_model=OrderRead, status_code=status.HTTP_202_ACCEPTED)
def create_order(
    payload: OrderCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.CUSTOMER, UserRole.ADMIN)),
):
    return OrderService(db).create_order(user, payload, idempotency_key)


@router.get("/{order_id}", response_model=OrderRead)
def get_order(order_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return OrderService(db).get_order(order_id, user)


@router.get("", response_model=Page[OrderRead])
def list_orders(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    repo = OrderRepository(db)
    if user.role == UserRole.CUSTOMER:
        items = repo.list_for_customer(user.id, limit, offset)
        total = len(items)
    else:
        items, total = repo.list(limit, offset)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.post("/{order_id}/cancel", response_model=OrderRead)
def cancel_order(
    order_id: UUID,
    reason: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return OrderService(db).cancel_order(order_id, user, reason)
