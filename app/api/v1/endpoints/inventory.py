from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.principal import Principal, get_principal
from app.core.exceptions import AuthorizationError, InventoryConflict, NotFoundError, WarehouseCodeConflict
from app.db.session import get_db
from app.models.entities import Inventory, Product, Warehouse
from app.repositories.inventory import InventoryRepository
from app.schemas.common import Page
from app.schemas.inventory import InventoryCreate, InventoryRead, InventoryUpdate, WarehouseCreate
from app.services.catalog import CatalogValidationError

router = APIRouter()


def is_unique_violation(exc: IntegrityError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == "23505" or getattr(exc.orig, "sqlite_errorcode", None) in {1555, 2067}


def tenant_scope(principal: Principal, scope: str) -> UUID:
    principal.require(scope)
    if principal.tenant_id is None:
        raise AuthorizationError("Tenant principal required")
    return principal.tenant_id


@router.post("/warehouses", status_code=201)
def create_warehouse(
    payload: WarehouseCreate, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
):
    tenant_id = tenant_scope(principal, "inventory:write")
    warehouse = Warehouse(tenant_id=tenant_id, **payload.model_dump())
    db.add(warehouse)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if is_unique_violation(exc):
            raise WarehouseCodeConflict("Warehouse code already exists") from exc
        raise
    db.refresh(warehouse)
    return warehouse


@router.post("", response_model=InventoryRead, status_code=201)
def create_inventory(
    payload: InventoryCreate, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)
):
    warehouse = db.scalar(
        select(Warehouse).where(Warehouse.id == payload.warehouse_id, Warehouse.tenant_id == principal.tenant_id)
    )
    if warehouse is None:
        raise NotFoundError("Warehouse not found")
    tenant_id = tenant_scope(principal, "inventory:write")
    product = db.scalar(select(Product).where(Product.tenant_id == tenant_id, Product.sku == payload.sku))
    if product is None:
        raise CatalogValidationError("product_required", "Inventory requires an existing product")
    item = Inventory(tenant_id=tenant_id, product_name=product.name, **payload.model_dump(exclude={"product_name"}))
    db.add(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if is_unique_violation(exc):
            raise InventoryConflict("Inventory already exists for this product and warehouse") from exc
        raise
    db.refresh(item)
    return item


@router.get("", response_model=Page[InventoryRead])
def list_inventory(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    tenant_id = tenant_scope(principal, "inventory:read")
    items, total = InventoryRepository(db).list_for_tenant(tenant_id, limit, offset)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/low-stock", response_model=list[InventoryRead])
def low_stock(db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    tenant_id = tenant_scope(principal, "inventory:read")
    rows, _ = InventoryRepository(db).list_for_tenant(tenant_id, 100, 0, low_stock=True)
    return rows


def scoped_inventory(db: Session, principal: Principal, inventory_id: UUID) -> Inventory:
    item = db.scalar(select(Inventory).where(Inventory.id == inventory_id, Inventory.tenant_id == principal.tenant_id))
    if item is None:
        raise NotFoundError("Inventory item not found")
    return item


@router.get("/{inventory_id}", response_model=InventoryRead)
def get_inventory(inventory_id: UUID, db: Session = Depends(get_db), principal: Principal = Depends(get_principal)):
    item = scoped_inventory(db, principal, inventory_id)
    principal.require("inventory:read")
    return item


@router.patch("/{inventory_id}", response_model=InventoryRead)
def update_inventory(
    inventory_id: UUID,
    payload: InventoryUpdate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    item = scoped_inventory(db, principal, inventory_id)
    principal.require("inventory:write")
    product = db.scalar(select(Product).where(Product.tenant_id == principal.tenant_id, Product.sku == item.sku))
    if product is None:
        raise CatalogValidationError("product_required", "Inventory requires an existing product")
    item.product_name = product.name
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    db.commit()
    return item
