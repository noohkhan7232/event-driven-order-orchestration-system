from pydantic import BaseModel

from app.models.enums import ShipmentStatus


class ShipmentEventCreate(BaseModel):
    status: ShipmentStatus
    location: str | None = None
    notes: str | None = None
