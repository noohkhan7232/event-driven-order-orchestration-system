from uuid import UUID

from pydantic import BaseModel, Field


class WarehouseCreate(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=2, max_length=120)
    region: str = Field(min_length=2, max_length=80)


class InventoryCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    product_name: str
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
