from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class WarehouseCreate(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=2, max_length=120)
    region: str = Field(min_length=2, max_length=80)


class WarehouseRead(BaseModel):
    id: UUID
    code: str
    name: str
    region: str

    model_config = {"from_attributes": True}


class InventoryCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    product_name: str | None = None
    warehouse_id: UUID
    available_quantity: int = Field(ge=0)
    low_stock_threshold: int = Field(default=10, ge=0)


class InventoryRead(BaseModel):
    id: UUID
    sku: str
    product_name: str
    warehouse_id: UUID
    available_quantity: int
    reserved_quantity: int
    low_stock_threshold: int

    model_config = {"from_attributes": True}


class InventoryUpdate(BaseModel):
    available_quantity: int | None = Field(default=None, ge=0)
    low_stock_threshold: int | None = Field(default=None, ge=0)

    @field_validator("available_quantity", "low_stock_threshold", mode="before")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Field may be omitted but cannot be null")
        return value


class InventoryAdjustment(BaseModel):
    delta: int = Field(strict=True)
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("delta")
    @classmethod
    def nonzero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("Delta must be nonzero")
        return value
