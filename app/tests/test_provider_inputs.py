from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.providers.contracts import PaymentAttempt, SessionOrder, minor_units


def test_provider_inputs_are_detached_exact_and_private():
    order = SessionOrder(uuid4(), uuid4(), Decimal("100.01"), "INR", ("SKU",), datetime.now(UTC))
    attempt = PaymentAttempt(uuid4(), 0)
    assert minor_units(order.amount) == 10001
    assert not hasattr(order, "buyer_email") and not hasattr(attempt, "order")
    for amount in [Decimal("1.001"), Decimal("-1")]:
        with pytest.raises(ValueError):
            minor_units(amount)
