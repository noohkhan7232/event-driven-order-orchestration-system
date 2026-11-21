from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.principal import Principal, get_principal
from app.db.session import get_db
from app.models.entities import ApiKey
from app.schemas.tenancy import ApiKeyCreate, ApiKeyCreated, ApiKeyRead, TenantRead, TenantRegister
from app.services.tenancy import create_api_key, register_tenant, revoke_api_key

router = APIRouter()


@router.post("/tenants/register", response_model=TenantRead, status_code=201)
def register(payload: TenantRegister, db: Session = Depends(get_db)):
    return register_tenant(db, payload)


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
def issue(payload: ApiKeyCreate, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    row, key = create_api_key(db, principal, payload)
    return {**ApiKeyRead.model_validate(row).model_dump(), "key": key}


@router.get("/api-keys", response_model=list[ApiKeyRead])
def list_keys(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    principal.require_owner()
    return list(db.scalars(select(ApiKey).where(ApiKey.tenant_id == principal.tenant_id).order_by(ApiKey.created_at)))


@router.delete("/api-keys/{key_id}", status_code=204)
def revoke(key_id: UUID, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    revoke_api_key(db, principal, key_id)
    return Response(status_code=204)
