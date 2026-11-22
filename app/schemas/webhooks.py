from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

EVENT_TYPES = frozenset(
    {
        "order.created",
        "order.paid",
        "order.shipped",
        "order.delivered",
        "order.failed",
        "order.cancelled",
        "order.status_changed",
        "payment.refunded",
        "ping",
    }
)
DeliveryStatus = Literal["pending", "delivered", "failed", "dead"]


def subscriptions(values: list[str]) -> list[str]:
    if not values or set(values) - EVENT_TYPES:
        raise ValueError("Unsupported webhook subscription")
    return sorted(set(values))


class EndpointCreate(BaseModel):
    model_config = {"extra": "forbid"}
    url: str = Field(min_length=1, max_length=2048)
    events: list[str] = Field(min_length=1, max_length=9)
    description: str | None = Field(default=None, max_length=200)

    @field_validator("events")
    @classmethod
    def valid_events(cls, value: list[str]) -> list[str]:
        return subscriptions(value)


class EndpointPatch(BaseModel):
    model_config = {"extra": "forbid"}
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    events: list[str] | None = Field(default=None, min_length=1, max_length=9)
    is_active: bool | None = None
    description: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def valid_patch(self):
        if any(getattr(self, key) is None for key in self.model_fields_set - {"description"}):
            raise ValueError("Webhook fields cannot be null")
        if self.events is not None:
            self.events = subscriptions(self.events)
        return self


class EndpointRead(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    url: str
    events: list[str]
    description: str | None
    is_active: bool
    consecutive_failures: int
    disabled_at: datetime | None
    disabled_reason: str | None
    created_at: datetime
    signing_secret_hint: str = Field(default="whsec_********")


class DeliveryRead(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    event_id: UUID
    event_type: str
    payload: dict
    status: DeliveryStatus
    attempts: int
    next_attempt_at: datetime
    last_status_code: int | None
    last_error: str | None
    delivered_at: datetime | None
    replay_count: int
    created_at: datetime
