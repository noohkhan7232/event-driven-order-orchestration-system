from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from pydantic import SecretStr

from app.core.exceptions import BusinessError, TransientError
from app.models.entities import Order, Payment, Shipment
from app.models.enums import PaymentStatus, ShipmentStatus


def minor_units(amount: Decimal) -> int:
    units = amount * 100
    if not units.is_finite() or units < 0 or units != units.to_integral_value():
        raise ValueError("Invalid minor-unit amount")
    return int(units)


@dataclass(frozen=True)
class SessionOrder:
    id: UUID
    tenant_id: UUID
    amount: Decimal
    currency: str
    skus: tuple[str, ...]
    expires_at: datetime


@dataclass(frozen=True)
class PaymentAttempt:
    id: UUID
    retry_attempt: int = 0
    provider_payment_id: str | None = None


@dataclass(frozen=True)
class SessionResult:
    provider_order_id: str
    checkout_payload: dict
    expires_at: datetime


@dataclass(frozen=True)
class ProviderEvent:
    event_id: str
    type: Literal["payment_captured", "payment_failed", "refund_processed", "refund_failed"]
    provider_order_id: str | None
    provider_payment_id: str
    provider_refund_id: str | None
    amount_minor: int
    currency: str
    raw: dict


@dataclass(frozen=True)
class ProviderRefund:
    provider_refund_id: str
    status: str


class PaymentProviderV2(Protocol):
    name: str

    def create_payment_session(
        self, order: SessionOrder, payment: PaymentAttempt, idempotency_key: str
    ) -> SessionResult: ...

    def verify_and_parse_webhook(
        self, headers: Mapping[str, str], raw_body: bytes, secret: SecretStr
    ) -> list[ProviderEvent]: ...

    def refund(self, payment: PaymentAttempt, amount_minor: int, idempotency_key: str) -> ProviderRefund: ...

    def fetch_payment_status(self, provider_order_id: str) -> list[ProviderEvent]: ...

    def fetch_refund(self, provider_refund_id: str) -> ProviderRefund: ...
    def find_refund(
        self, payment: PaymentAttempt, amount_minor: int, idempotency_key: str
    ) -> ProviderRefund | None: ...


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
