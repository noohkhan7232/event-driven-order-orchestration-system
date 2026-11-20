from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, DomainError, NotFoundError
from app.models.entities import Product, Tenant
from app.schemas.products import ProductCreate, ProductUpdate


class CatalogValidationError(DomainError):
    status_code = 422

    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        super().__init__(message, details)


def get_product(db: Session, tenant_id: UUID | None, product_id: UUID) -> Product:
    row = db.scalar(select(Product).where(Product.tenant_id == tenant_id, Product.id == product_id))
    if row is None:
        raise NotFoundError("Product not found")
    return row


def products_for_order(db: Session, tenant_id: UUID, skus: set[str]) -> dict[str, Product]:
    rows = list(db.scalars(select(Product).where(Product.tenant_id == tenant_id, Product.sku.in_(skus))))
    result = {row.sku: row for row in rows if row.is_active}
    missing = sorted(skus - result.keys())
    if missing:
        raise CatalogValidationError("unknown_or_inactive_sku", "Unknown or inactive products", {"skus": missing})
    return result


def create_product(db: Session, tenant_id: UUID, payload: ProductCreate) -> Product:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFoundError("Store not found")
    data = payload.model_dump()
    data["currency"] = payload.currency or tenant.default_currency
    row = Product(tenant_id=tenant_id, **data)
    try:
        db.add(row)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("Product SKU already exists in this tenant") from exc
    return row


def update_product(db: Session, row: Product, payload: ProductUpdate) -> Product:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    db.commit()
    return row


def list_products(
    db: Session, tenant_id: UUID, limit: int, offset: int, is_active: bool | None, q: str | None
) -> tuple[list[Product], int]:
    conditions = [Product.tenant_id == tenant_id]
    if is_active is not None:
        conditions.append(Product.is_active == is_active)
    if q:
        pattern = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append(or_(Product.sku.ilike(pattern, escape="\\"), Product.name.ilike(pattern, escape="\\")))
    total = db.scalar(select(func.count()).select_from(Product).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Product)
            .where(*conditions)
            .order_by(Product.created_at.desc(), Product.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total
