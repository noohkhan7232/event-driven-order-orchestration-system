from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=180)
    description: str | None = None
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = None
    price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    is_active: bool | None = None

    @field_validator("name", "price", "is_active", mode="before")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Field may be omitted but cannot be null")
        return value


class ProductRead(BaseModel):
    id: UUID
    sku: str
    name: str
    description: str | None
    price: Decimal
    currency: str
    is_active: bool
    model_config = {"from_attributes": True}
