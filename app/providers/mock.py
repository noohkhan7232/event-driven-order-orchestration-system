from hashlib import sha256

from app.core.config import get_settings
from app.models.entities import Order, Payment, Shipment
from app.models.enums import PaymentStatus, ShipmentStatus
from app.providers.contracts import (
    PaymentProvider,
    PaymentResult,
    ProviderDeclined,
    ProviderUnavailable,
    RefundResult,
    ShipmentResult,
    ShippingProvider,
)


def reference(key: str) -> str:
    return "mock_" + sha256(key.encode()).hexdigest()[:32]


class MockPaymentProvider(PaymentProvider):
    name = "mock"

    def authorize(self, order: Order, *, idempotency_key: str, retry_attempt: int = 0) -> PaymentResult:
        settings = get_settings()
        if settings.failure_injection_enabled:
            if any(item.sku.startswith("FAIL-PAYMENT-") for item in order.items):
                raise ProviderDeclined("Payment declined")
            transient = [item for item in order.items if item.sku.startswith("FAIL-TRANSIENT-")]
            attempts = (
                settings.task_max_retries + 1
                if any(item.sku.startswith("FAIL-TRANSIENT-EXHAUST-") for item in transient)
                else settings.failure_transient_attempts
            )
            if transient and retry_attempt < attempts:
                raise ProviderUnavailable("Injected provider unavailable")
        return PaymentResult(PaymentStatus.AUTHORIZED, reference(idempotency_key), {"simulated": True})

    def refund(self, order: Order, payment: Payment, *, idempotency_key: str) -> RefundResult:
        return RefundResult(PaymentStatus.REFUNDED, reference(idempotency_key), {"simulated": True})


class MockShippingProvider(ShippingProvider):
    name = "mock"

    def create_shipment(self, order: Order, *, idempotency_key: str) -> ShipmentResult:
        if get_settings().failure_injection_enabled and any(
            item.sku.startswith("FAIL-SHIPMENT-") for item in order.items
        ):
            raise ProviderDeclined("Shipment declined")
        return ShipmentResult(ShipmentStatus.CREATED, reference(idempotency_key), {"simulated": True})

    def cancel_shipment(self, order: Order, shipment: Shipment, *, idempotency_key: str) -> None:
        return None
