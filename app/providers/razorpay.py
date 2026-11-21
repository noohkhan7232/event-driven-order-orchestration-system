"""Razorpay Orders and Refunds API. All arguments are detached payment snapshots."""

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Literal

import httpx
from pydantic import SecretStr

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


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("Invalid provider identifier")
    return value


def normalize(event: str, event_id: str, payment: dict, refund: dict) -> list[ProviderEvent]:
    kind: Literal["payment_captured", "payment_failed", "refund_processed", "refund_failed"]
    if event in {"payment.captured", "order.paid", "payment.failed"}:
        kind = "payment_failed" if event == "payment.failed" else "payment_captured"
        entity = payment
        refund_id = None
    elif event in {"refund.created", "refund.processed", "refund.failed"}:
        if event == "refund.created" and refund["status"] != "processed":
            return []
        kind = "refund_failed" if event == "refund.failed" else "refund_processed"
        entity = refund
        refund_id = identifier(refund["id"])
    else:
        return []
    amount, currency = entity["amount"], entity["currency"]
    if type(amount) is not int or amount < 0 or not isinstance(currency, str) or not re.fullmatch("[A-Z]{3}", currency):
        raise ValueError("Invalid payment amount or currency")
    return [
        ProviderEvent(
            identifier(event_id),
            kind,
            identifier(payment["order_id"]) if payment.get("order_id") else None,
            identifier(refund["payment_id"] if refund_id else payment["id"]),
            refund_id,
            amount,
            currency,
            {"event": event},
        )
    ]


def parse_signed(headers: Mapping[str, str], raw_body: bytes, secret: SecretStr) -> list[ProviderEvent]:
    fields = {k.lower(): v for k, v in headers.items()}
    expected = hmac.new(secret.get_secret_value().encode(), raw_body, "sha256").hexdigest().encode()
    if len(raw_body) > 65536 or not hmac.compare_digest(expected, fields.get("x-razorpay-signature", "").encode()):
        raise ProviderDeclined("Invalid webhook")
    try:
        value = json.loads(raw_body)
        payload = value["payload"]
        return normalize(
            value["event"],
            identifier(fields["x-razorpay-event-id"]),
            payload.get("payment", {}).get("entity", {}),
            payload.get("refund", {}).get("entity", {}),
        )
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ProviderDeclined("Invalid webhook") from None


class RazorpayProvider:
    name = "razorpay"
    verify_and_parse_webhook = staticmethod(parse_signed)

    def __init__(self, key_id: str, key_secret: SecretStr, transport: httpx.BaseTransport | None = None):
        self.key_id, self._secret, self._transport = key_id, key_secret, transport

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            with httpx.Client(
                base_url="https://api.razorpay.com/v1/",
                auth=(self.key_id, self._secret.get_secret_value()),
                timeout=httpx.Timeout(10, connect=3),
                transport=self._transport,
                trust_env=False,
            ) as client:
                response = client.request(method, path, **kwargs)
            if response.status_code in {408, 409, 429} or response.status_code >= 500:
                raise ProviderUnavailable("Payment provider temporarily unavailable")
            if not response.is_success:
                raise ProviderDeclined("Payment provider rejected request")
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (httpx.HTTPError, ValueError):
            raise ProviderUnavailable("Payment provider temporarily unavailable") from None

    def create_payment_session(
        self, order: SessionOrder, payment: PaymentAttempt, idempotency_key: str
    ) -> SessionResult:
        if order.currency != "INR":
            raise ProviderDeclined("unsupported_currency")
        receipt = payment.id.hex
        amount = minor_units(order.amount)
        notes = {"order_id": str(order.id), "tenant_id": str(order.tenant_id)}
        try:
            rows = self._request("GET", "orders", params={"receipt": receipt})["items"]
            row = next((item for item in rows if item["receipt"] == receipt), None)
            if row is None:
                row = self._request(
                    "POST", "orders", json={"amount": amount, "currency": "INR", "receipt": receipt, "notes": notes}
                )
            if (
                row["amount"] != amount
                or row["currency"] != "INR"
                or row["receipt"] != receipt
                or row["notes"] != notes
            ):
                raise ValueError
            order_id = identifier(row["id"])
            return SessionResult(
                order_id,
                {
                    "provider": self.name,
                    "provider_order_id": order_id,
                    "amount_minor": amount,
                    "currency": "INR",
                    "key_id": self.key_id,
                },
                order.expires_at,
            )
        except (ValueError, TypeError, KeyError):
            raise ProviderUnavailable("Invalid provider order response") from None

    def refund(self, payment: PaymentAttempt, amount_minor: int, idempotency_key: str) -> ProviderRefund:
        pid = identifier(payment.provider_payment_id)
        receipt = hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
        try:
            skip = 0
            while True:
                rows = self._request("GET", f"payments/{pid}/refunds", params={"skip": skip, "count": 100})["items"]
                found = next((r for r in rows if r.get("receipt") == receipt), None)
                if found is not None:
                    break
                if len(rows) < 100:
                    found = self._request(
                        "POST",
                        f"payments/{pid}/refund",
                        json={"amount": amount_minor, "speed": "normal", "receipt": receipt},
                        headers={"X-Refund-Idempotency": receipt},
                    )
                    break
                skip += len(rows)
            if found["amount"] != amount_minor or found["payment_id"] != pid:
                raise ValueError
            return ProviderRefund(identifier(found["id"]), found["status"])
        except (KeyError, TypeError, ValueError):
            raise ProviderUnavailable("Invalid provider refund response") from None

    def fetch_refund(self, provider_refund_id: str) -> ProviderRefund:
        row = self._request("GET", f"refunds/{identifier(provider_refund_id)}")
        try:
            return ProviderRefund(identifier(row["id"]), row["status"])
        except (KeyError, TypeError, ValueError):
            raise ProviderUnavailable("Invalid provider refund response") from None

    def fetch_payment_status(self, provider_order_id: str) -> list[ProviderEvent]:
        rows = self._request("GET", f"orders/{identifier(provider_order_id)}/payments")
        events = []
        try:
            for row in rows["items"]:
                if row["status"] in {"captured", "failed"} and row["order_id"] == provider_order_id:
                    events.extend(
                        normalize("payment." + row["status"], "fetch_" + row["id"] + "_" + row["status"], row, {})
                    )
            return events
        except (TypeError, KeyError, ValueError):
            raise ProviderUnavailable("Invalid provider payment response") from None
