"""Local simulator using the same signed normalized event contract as the adapter."""

import hashlib
import hmac
import json
from threading import Lock
from typing import ClassVar

from app.core.config import Settings
from app.providers.contracts import (
    PaymentAttempt,
    ProviderDeclined,
    ProviderEvent,
    ProviderRefund,
    ProviderUnavailable,
    SessionOrder,
    SessionResult,
    minor_units,
)
from app.providers.mock import reference
from app.providers.razorpay import parse_signed


class MockPaymentProviderV2:
    name = "mock"
    verify_and_parse_webhook = staticmethod(parse_signed)
    _refund_lock: ClassVar = Lock()
    _attempts: ClassVar[dict[str, int]] = {}
    _refunds: ClassVar[dict[str, ProviderRefund]] = {}
    _refund_amounts: ClassVar[dict[str, int]] = {}
    _lock: ClassVar = Lock()
    _captures: ClassVar[dict[str, ProviderEvent]] = {}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create_payment_session(
        self, order: SessionOrder, payment: PaymentAttempt, idempotency_key: str
    ) -> SessionResult:
        if self.settings.failure_injection_enabled:
            if any(sku.startswith("FAIL-PAYMENT-") for sku in order.skus):
                raise ProviderDeclined("PAYMENT_DECLINED")
            if any(sku.startswith("FAIL-TRANSIENT-") for sku in order.skus):
                exhausted = any(sku.startswith("FAIL-TRANSIENT-EXHAUST-") for sku in order.skus)
                attempts = self.settings.task_max_retries + 1 if exhausted else self.settings.failure_transient_attempts
                if payment.retry_attempt < attempts:
                    raise ProviderUnavailable("Injected provider unavailable")
        oid = reference(idempotency_key)
        return SessionResult(
            oid,
            {
                "provider": self.name,
                "provider_order_id": oid,
                "key_id": "mock_public",
                "amount_minor": minor_units(order.amount),
                "currency": order.currency,
            },
            order.expires_at,
        )

    def signed_capture(self, session: SessionResult) -> tuple[dict[str, str], bytes]:
        oid = session.provider_order_id
        body = json.dumps(
            {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": reference(oid + ":captured"),
                            "order_id": oid,
                            "amount": session.checkout_payload["amount_minor"],
                            "currency": session.checkout_payload["currency"],
                        }
                    }
                },
            },
            separators=(",", ":"),
        ).encode()
        signature = hmac.new(
            self.settings.mock_webhook_secret.get_secret_value().encode(), body, hashlib.sha256
        ).hexdigest()
        headers = {"X-Razorpay-Signature": signature, "X-Razorpay-Event-Id": reference(oid + ":event")}
        with self._lock:
            self._captures[oid] = parse_signed(headers, body, self.settings.mock_webhook_secret)[0]
        return headers, body

    def refund(self, payment: PaymentAttempt, amount_minor: int, idempotency_key: str) -> ProviderRefund:
        rid = reference(f"{payment.id}:{idempotency_key}")
        with self._refund_lock:
            if type(amount_minor) is not int or amount_minor <= 0:
                raise ProviderDeclined("Invalid refund amount")
            if rid in self._refund_amounts and self._refund_amounts[rid] != amount_minor:
                raise ProviderDeclined("Refund idempotency conflict")
            self._refund_amounts[rid] = amount_minor
            if rid not in self._refunds:
                attempts = self._attempts.get(rid, 0)
                self._attempts[rid] = attempts + 1
                if attempts < self.settings.mock_refund_fail_attempts:
                    raise ProviderUnavailable("Injected refund unavailable")
                self._refunds[rid] = ProviderRefund(rid, "processed")
            return self._refunds[rid]

    def fetch_refund(self, provider_refund_id: str) -> ProviderRefund:
        with self._refund_lock:
            if provider_refund_id not in self._refunds:
                raise ProviderDeclined("Refund not found")
            return self._refunds[provider_refund_id]

    def fetch_payment_status(self, provider_order_id: str) -> list[ProviderEvent]:
        with self._lock:
            return [self._captures[provider_order_id]] if provider_order_id in self._captures else []
