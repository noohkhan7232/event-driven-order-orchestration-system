from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import OrderStatus, PaymentStatus, ShipmentStatus


class OrderItemCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=500)


class OrderCreate(BaseModel):
    items: list[OrderItemCreate] = Field(min_length=1, max_length=100)
    currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    external_reference: str | None = Field(default=None, min_length=1, max_length=100)
    buyer_name: str | None = Field(default=None, min_length=1, max_length=120)
    buyer_email: EmailStr | None = Field(default=None, max_length=255)
    buyer_phone: str | None = Field(default=None, max_length=20, pattern=r"^\+?[0-9][0-9 ()-]{3,18}$")


class OrderItemRead(BaseModel):
    id: UUID
    sku: str
    product_name: str
    quantity: int
    unit_price: Decimal
    warehouse_id: UUID | None

    model_config = {"from_attributes": True}


class PaymentRead(BaseModel):
    id: UUID
    provider: str
    provider_transaction_id: str | None
    status: PaymentStatus
    amount: Decimal
    failure_code: str | None

    model_config = {"from_attributes": True}


class ShipmentRead(BaseModel):
    id: UUID
    carrier: str
    tracking_number: str | None
    status: ShipmentStatus

    model_config = {"from_attributes": True}


class OrderRead(BaseModel):
    external_reference: str | None
    buyer_name: str | None
    buyer_email: str | None
    buyer_phone: str | None
    id: UUID
    status: OrderStatus
    total_amount: Decimal
    currency: str
    failure_reason: str | None
    items: list[OrderItemRead]
    payments: list[PaymentRead] = []
    shipments: list[ShipmentRead] = []

    model_config = {"from_attributes": True}


class StatusUpdate(BaseModel):
    status: OrderStatus
    reason: str | None = None
