from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import require_roles
from app.db.session import get_db
from app.models.entities import Inventory, User, Warehouse
from app.models.enums import UserRole
from app.repositories.inventory import InventoryRepository
from app.schemas.common import Page
from app.schemas.inventory import InventoryCreate, InventoryRead, WarehouseCreate

router = APIRouter()


@router.post("/warehouses", status_code=status.HTTP_201_CREATED)
def create_warehouse(
    payload: WarehouseCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.VENDOR)),
):
    warehouse = Warehouse(**payload.model_dump())
    db.add(warehouse)
    db.commit()
    db.refresh(warehouse)
    return warehouse


@router.post("", response_model=InventoryRead, status_code=status.HTTP_201_CREATED)
def create_inventory(
    payload: InventoryCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.VENDOR)),
):
    item = Inventory(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("", response_model=Page[InventoryRead])
def list_inventory(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.VENDOR)),
):
    items, total = InventoryRepository(db).list(limit, offset)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/low-stock", response_model=list[InventoryRead])
def low_stock(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(UserRole.ADMIN, UserRole.VENDOR)),
):
    rows, _ = InventoryRepository(db).list(limit=100, offset=0)
    return [row for row in rows if row.available_quantity <= row.low_stock_threshold]
