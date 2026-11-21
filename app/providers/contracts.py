from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.exceptions import BusinessError, TransientError
from app.models.entities import Order, Payment, Shipment
from app.models.enums import PaymentStatus, ShipmentStatus


class ProviderDeclined(BusinessError):
    code = "provider_declined"


class ProviderUnavailable(TransientError):
    pass


@dataclass(frozen=True)
class PaymentResult:
    status: PaymentStatus
    provider_reference: str
    raw: dict


@dataclass(frozen=True)
class RefundResult:
    status: PaymentStatus
    provider_reference: str
    raw: dict


@dataclass(frozen=True)
class ShipmentResult:
    status: ShipmentStatus
    provider_reference: str
    raw: dict


class PaymentProvider(ABC):
    name: str

    @abstractmethod
    def authorize(self, order: Order, *, idempotency_key: str, retry_attempt: int = 0) -> PaymentResult:
        raise NotImplementedError

    @abstractmethod
    def refund(self, order: Order, payment: Payment, *, idempotency_key: str) -> RefundResult:
        raise NotImplementedError


class ShippingProvider(ABC):
    name: str

    @abstractmethod
    def create_shipment(self, order: Order, *, idempotency_key: str) -> ShipmentResult:
        raise NotImplementedError

    @abstractmethod
    def cancel_shipment(self, order: Order, shipment: Shipment, *, idempotency_key: str) -> None:
        raise NotImplementedError
