from app.core.config import get_settings
from app.providers.contracts import PaymentProvider, ShippingProvider
from app.providers.mock import MockPaymentProvider, MockShippingProvider


def get_payment_provider() -> PaymentProvider:
    settings = get_settings()
    if settings.payment_provider != "mock":
        raise ValueError("Unknown payment provider")
    return MockPaymentProvider()


def get_shipping_provider() -> ShippingProvider:
    settings = get_settings()
    if settings.shipping_provider != "mock":
        raise ValueError("Unknown shipping provider")
    return MockShippingProvider()
