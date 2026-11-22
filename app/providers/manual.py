"""Seller-managed courier: local identifiers only, never external I/O."""

from hashlib import sha256

from app.models.entities import Order, Shipment
from app.models.enums import ShipmentStatus
from app.providers.contracts import ShipmentResult, ShippingProvider


class ManualShippingProvider(ShippingProvider):
    name = "manual"

    def create_shipment(self, order: Order, *, idempotency_key: str) -> ShipmentResult:
        return ShipmentResult(ShipmentStatus.CREATED, "manual_" + sha256(idempotency_key.encode()).hexdigest()[:32], {})

    def cancel_shipment(self, order: Order, shipment: Shipment, *, idempotency_key: str) -> None:
        return None
