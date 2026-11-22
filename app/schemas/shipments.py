from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from app.models.enums import ShipmentStatus


class ShipmentEventCreate(BaseModel):
    status: ShipmentStatus
    location: str | None = None
    notes: str | None = None


class ShipmentUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    carrier: str | None = Field(default=None, min_length=1, max_length=60)
    tracking_number: str | None = Field(default=None, min_length=1, max_length=80)
    tracking_url: str | None = Field(default=None, max_length=300)
    estimated_delivery: date | None = None

    @field_validator("tracking_url")
    @classmethod
    def https_tracking(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                url = urlsplit(value)
                if (
                    url.scheme != "https"
                    or not url.hostname
                    or url.username is not None
                    or url.password is not None
                    or any(ord(c) < 33 for c in value)
                ):
                    raise ValueError
                _ = url.port
            except ValueError:
                raise ValueError("Tracking URL must be HTTPS without credentials") from None
        return value


class ShippingSettings(BaseModel):
    shipping_provider: Literal["mock", "manual"]
