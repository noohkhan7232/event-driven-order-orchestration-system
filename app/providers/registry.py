from app.core.config import get_settings
from app.providers.contracts import PaymentProvider, ShippingProvider
from app.providers.manual import ManualShippingProvider
from app.providers.mock import MockPaymentProvider, MockShippingProvider


def get_payment_provider() -> PaymentProvider:
    settings = get_settings()
    if settings.payment_provider != "mock":
        raise ValueError("Unknown payment provider")
    return MockPaymentProvider()


def get_shipping_provider(name: str | None = None) -> ShippingProvider:
    settings = get_settings()
    name = name or settings.shipping_provider
    if name == "manual":
        return ManualShippingProvider()
    if name != "mock":
        raise ValueError("Unknown shipping provider")
    if settings.environment not in {"local", "development", "test"}:
        raise ValueError("Mock shipping is forbidden outside local/development/test")
    return MockShippingProvider()
