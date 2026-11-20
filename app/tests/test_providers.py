import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.enums import PaymentStatus, ShipmentStatus


def test_provider_registry_rejects_unknown_and_nonlocal_mock():
    with pytest.raises(ValidationError, match="provider"):
        Settings(_env_file=None, payment_provider="unknown")
    with pytest.raises(ValidationError, match="mock"):
        Settings(_env_file=None, environment="production", secret_key="x" * 32)
    assert Settings(_env_file=None, environment="production", secret_key="x" * 32, allow_mock_providers=True)


@pytest.mark.parametrize("kind", ["payment", "shipping"])
def test_registry_defends_against_invalid_runtime_configuration(monkeypatch, kind):
    from app.providers import registry

    settings = Settings(_env_file=None).model_copy(update={f"{kind}_provider": "unknown"})
    monkeypatch.setattr(registry, "get_settings", lambda: settings)
    with pytest.raises(ValueError, match="Unknown"):
        getattr(registry, f"get_{kind}_provider")()


def test_mock_provider_deterministic_references_and_safe_metadata(db, workflow):
    from app.providers.registry import get_payment_provider, get_shipping_provider

    order, _ = workflow
    payment = get_payment_provider()
    first = payment.authorize(order, idempotency_key="same")
    second = payment.authorize(order, idempotency_key="same")
    assert first == second and first.status == PaymentStatus.AUTHORIZED and first.raw == {"simulated": True}
    shipping = get_shipping_provider()
    result = shipping.create_shipment(order, idempotency_key="shipping")
    assert result.status == ShipmentStatus.CREATED and result.provider_reference


def test_provider_calls_persist_references_and_idempotency_keys(db, workflow, monkeypatch):
    from app.providers.contracts import PaymentResult, RefundResult, ShipmentResult
    from app.services import payments, shipping

    order, _ = workflow
    calls = []

    class PaymentAdapter:
        name = "test"

        def authorize(self, order, *, idempotency_key, retry_attempt=0):
            calls.append(idempotency_key)
            return PaymentResult(PaymentStatus.AUTHORIZED, "payment-ref", {})

        def refund(self, order, payment, *, idempotency_key):
            calls.append(idempotency_key)
            return RefundResult(PaymentStatus.REFUNDED, "refund-ref", {})

    class ShippingAdapter:
        name = "test"

        def create_shipment(self, order, *, idempotency_key):
            calls.append(idempotency_key)
            return ShipmentResult(ShipmentStatus.CREATED, "shipment-ref", {})

        def cancel_shipment(self, order, shipment, *, idempotency_key):
            calls.append(idempotency_key)

    monkeypatch.setattr(payments, "get_payment_provider", lambda: PaymentAdapter())
    monkeypatch.setattr(shipping, "get_shipping_provider", lambda: ShippingAdapter())
    payment = payments.PaymentService(db).authorize(order)
    db.flush()
    db.refresh(order)
    shipment = shipping.ShippingService(db).create_shipment(order)
    db.flush()
    db.refresh(order)
    payments.PaymentService(db).refund(order)
    shipping.ShippingService(db).cancel_for_order(order)
    db.flush()
    assert payment.provider == shipment.provider == "test"
    assert payment.provider_reference == "payment-ref" and shipment.provider_reference == "shipment-ref"
    assert payment.status == PaymentStatus.REFUNDED and shipment.status == ShipmentStatus.CANCELLED
    assert calls == [
        f"{order.id}:authorize:0",
        f"{order.id}:shipment:0",
        f"{order.id}:refund:{payment.id}",
        f"{order.id}:cancel:{shipment.id}",
    ]
