from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator

from app.models.entities import Tenant

Scope = Literal["orders:read", "orders:write", "catalog:read", "catalog:write", "inventory:read", "inventory:write"]


class TenantRegister(BaseModel):
    invite_code: SecretStr | None = None
    business_name: str = Field(min_length=2, max_length=160)
    slug: str | None = None
    owner_email: EmailStr
    owner_password: str = Field(min_length=8, max_length=128)
    owner_name: str = Field(min_length=2, max_length=160)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str | None) -> str | None:
        if value is not None:
            Tenant(name="validation", slug=value)
        return value


class TenantRead(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str
    default_currency: str
    model_config = {"from_attributes": True}


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[Scope]
    expires_at: datetime | None = None


class ApiKeyRead(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyRead):
    key: str
