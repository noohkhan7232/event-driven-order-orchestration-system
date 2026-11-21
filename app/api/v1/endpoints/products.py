from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.endpoints.inventory import tenant_scope
from app.auth.principal import Principal, get_principal
from app.db.session import get_db
from app.schemas.common import Page
from app.schemas.products import ProductCreate, ProductRead, ProductUpdate
from app.services.catalog import create_product, get_product, list_products, update_product

router = APIRouter()


@router.post("", response_model=ProductRead, status_code=201)
def create(payload: ProductCreate, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return create_product(db, tenant_scope(principal, "catalog:write"), payload)


@router.get("", response_model=Page[ProductRead])
def listing(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    is_active: bool | None = None,
    q: str | None = Query(default=None, max_length=180),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    items, total = list_products(db, tenant_scope(principal, "catalog:read"), limit, offset, is_active, q)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{product_id}", response_model=ProductRead)
def get(product_id: UUID, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    row = get_product(db, principal.tenant_id, product_id)
    principal.require("catalog:read")
    return row


@router.patch("/{product_id}", response_model=ProductRead)
def patch(
    product_id: UUID,
    payload: ProductUpdate,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    row = get_product(db, principal.tenant_id, product_id)
    principal.require("catalog:write")
    return update_product(db, row, payload)
