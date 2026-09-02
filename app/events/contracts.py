from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class DomainEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    aggregate_id: UUID
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict
    correlation_id: str | None = None


class EventNames:
    ORDER_CREATED = "order.created"
    INVENTORY_RESERVED = "inventory.reserved"
    INVENTORY_FAILED = "inventory.failed"
    PAYMENT_AUTHORIZED = "payment.authorized"
    PAYMENT_FAILED = "payment.failed"
    SHIPMENT_CREATED = "shipment.created"
    SHIPMENT_DELAYED = "shipment.delayed"
    ORDER_COMPLETED = "order.completed"
