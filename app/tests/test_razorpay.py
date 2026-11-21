import hmac
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.providers.contracts import PaymentAttempt, ProviderDeclined, ProviderUnavailable, SessionOrder
from app.providers.razorpay import RazorpayProvider


def test_razorpay_session_recovers_unknown_outcome_by_receipt():
    rows = []
    order = SessionOrder(uuid4(), uuid4(), Decimal("121.03"), "INR", ("SKU",), datetime.now(UTC))
    payment = PaymentAttempt(uuid4())

    def transport(req):
        assert req.url.host == "api.razorpay.com"
        if req.method == "GET":
            assert req.url.params["receipt"] == payment.id.hex
            return httpx.Response(200, json={"items": rows})
        body = json.loads(req.content)
        assert body == {
            "receipt": payment.id.hex,
            "amount": 12103,
            "currency": "INR",
            "notes": {"order_id": str(order.id), "tenant_id": str(order.tenant_id)},
        }
        rows.append({"id": "order_one", **body})
        raise httpx.ReadTimeout("lost response")

    adapter = RazorpayProvider("public1234", SecretStr("synthetic"), httpx.MockTransport(transport))
    with pytest.raises(ProviderUnavailable):
        adapter.create_payment_session(order, payment, "same")
    session = adapter.create_payment_session(order, payment, "same")
    assert session.checkout_payload == {
        "provider": "razorpay",
        "provider_order_id": "order_one",
        "amount_minor": 12103,
        "currency": "INR",
        "key_id": "public1234",
    }
    assert len(rows) == 1


@pytest.mark.parametrize(
    "event,expected",
    [
        ("payment.captured", "payment_captured"),
        ("payment.failed", "payment_failed"),
        ("order.paid", "payment_captured"),
        ("refund.processed", "refund_processed"),
        ("refund.failed", "refund_failed"),
        ("refund.created", "refund_processed"),
    ],
)
def test_signed_events_are_normalized_without_pii(event, expected):
    secret = SecretStr("synthetic")
    p = {"id": "pay_one", "order_id": "order_one", "amount": 100, "currency": "INR", "email": "private@example.com"}
    r = {"id": "rfnd_one", "payment_id": "pay_one", "amount": 50, "currency": "INR", "status": "processed"}
    body = json.dumps({"event": event, "payload": {"payment": {"entity": p}, "refund": {"entity": r}}}).encode()
    headers = {
        "X-Razorpay-Signature": hmac.new(b"synthetic", body, "sha256").hexdigest(),
        "X-Razorpay-Event-Id": "evt_one",
    }
    adapter = RazorpayProvider("public1234", secret)
    (normalized,) = adapter.verify_and_parse_webhook(headers, body, secret)
    assert (
        normalized.type == expected
        and normalized.provider_payment_id == "pay_one"
        and normalized.provider_order_id == "order_one"
    )
    assert normalized.amount_minor == (50 if event.startswith("refund") else 100)
    assert "private" not in repr(normalized)
    for invalid, changed in [({}, body), (headers, body + b" "), (headers, b"x" * 65537)]:
        with pytest.raises(ProviderDeclined):
            adapter.verify_and_parse_webhook(invalid, changed, secret)


@pytest.mark.parametrize(
    "status,result,error",
    [
        (429, {}, ProviderUnavailable),
        (500, {}, ProviderUnavailable),
        (400, {}, ProviderDeclined),
        (200, [], ProviderUnavailable),
        (200, {}, ProviderUnavailable),
    ],
)
def test_provider_failures_hide_raw_response(status, result, error):
    adapter = RazorpayProvider(
        "public", SecretStr("synthetic"), httpx.MockTransport(lambda _: httpx.Response(status, json=result))
    )
    with pytest.raises(error):
        adapter.fetch_refund("rfnd_safe")
    with pytest.raises(error):
        adapter.fetch_payment_status("order_safe")


def test_unsupported_currency_and_invalid_signed_envelope():
    adapter = RazorpayProvider("public", SecretStr("synthetic"))
    with pytest.raises(ProviderDeclined, match="unsupported_currency"):
        adapter.create_payment_session(
            SessionOrder(uuid4(), uuid4(), Decimal(1), "USD", (), datetime.now(UTC)), PaymentAttempt(uuid4()), "key"
        )
    body = b"{}"
    with pytest.raises(ProviderDeclined, match="Invalid webhook"):
        adapter.verify_and_parse_webhook(
            {"x-razorpay-signature": hmac.new(b"synthetic", body, "sha256").hexdigest()}, body, SecretStr("synthetic")
        )


def test_refunds_reconcile_and_fetch():
    rows = []

    def handler(req):
        if req.url.path.endswith("/refunds"):
            return httpx.Response(200, json={"items": rows})
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["receipt"] == req.headers["X-Refund-Idempotency"]
            rows.append({"id": "rfnd_one", "status": "processed", "payment_id": "pay_one", **body})
            return httpx.Response(200, json=rows[0])
        if req.url.path.endswith("/payments"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "status": "captured",
                            "amount": 100,
                            "currency": "INR",
                            "id": "pay_one",
                            "order_id": "order_one",
                        }
                    ]
                },
            )
        return httpx.Response(200, json=rows[0])

    adapter = RazorpayProvider("public", SecretStr("synthetic"), httpx.MockTransport(handler))
    p = PaymentAttempt(uuid4(), provider_payment_id="pay_one")
    assert adapter.refund(p, 100, "key") == adapter.refund(p, 100, "key") == adapter.fetch_refund("rfnd_one")
    assert len(rows) == 1
    assert adapter.fetch_payment_status("order_one")[0].type == "payment_captured"


@pytest.mark.parametrize("event", ["unhandled.event", "refund.created"])
def test_unsupported_or_pending_refund_notifications_are_ignored(event):
    from app.providers.razorpay import parse_signed

    raw = json.dumps({"event": event, "payload": {"refund": {"entity": {"status": "pending"}}}}).encode()
    headers = {
        "x-razorpay-event-id": "evt_ignored",
        "x-razorpay-signature": hmac.new(b"test", raw, "sha256").hexdigest(),
    }
    assert parse_signed(headers, raw, SecretStr("test")) == []


@pytest.mark.parametrize("amount,payment_id", [(True, "pay_ok"), (100, "pay/unsafe")])
def test_signed_but_invalid_financial_entities_are_rejected(amount, payment_id):
    from app.providers.razorpay import parse_signed

    entity = {"id": payment_id, "order_id": "order_ok", "amount": amount, "currency": "INR"}
    raw = json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": entity}}}).encode()
    headers = {
        "x-razorpay-event-id": "evt_invalid",
        "x-razorpay-signature": hmac.new(b"test", raw, "sha256").hexdigest(),
    }
    with pytest.raises(ProviderDeclined, match="Invalid webhook"):
        parse_signed(headers, raw, SecretStr("test"))


@pytest.mark.skipif(
    not (os.getenv("RAZORPAY_TEST_KEY_ID") and os.getenv("RAZORPAY_TEST_KEY_SECRET")),
    reason="Razorpay test credentials absent",
)
def test_razorpay_test_mode_session():
    key = os.environ["RAZORPAY_TEST_KEY_ID"]
    assert key.startswith("rzp_test_")
    adapter = RazorpayProvider(key, SecretStr(os.environ["RAZORPAY_TEST_KEY_SECRET"]))
    result = adapter.create_payment_session(
        SessionOrder(uuid4(), uuid4(), Decimal("1.00"), "INR", (), datetime.now(UTC)), PaymentAttempt(uuid4()), "test"
    )
    assert result.provider_order_id.startswith("order_")
