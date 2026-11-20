import ast
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.models.entities import Order
from app.models.enums import OrderStatus

ALLOWED = {
    "created": {"inventory_reserved", "failed", "cancelled"},
    "inventory_reserved": {"payment_pending", "failed", "cancelled"},
    "payment_pending": {"paid", "failed", "cancelled"},
    "paid": {"fulfillment_requested", "failed", "cancelled"},
    "fulfillment_requested": {"shipped", "cancelled"},
    "shipped": {"delivered"},
}


@pytest.mark.parametrize("before", list(OrderStatus))
@pytest.mark.parametrize("after", list(OrderStatus))
def test_all_order_transitions(before, after):
    from app.core.exceptions import InvalidTransitionError
    from app.domain.order_state import transition

    order = Order(id=uuid4(), status=before, idempotency_key="state")
    db = Mock()
    if after.value in ALLOWED.get(before.value, set()):
        transition(order, after, db=db, reason="TEST", actor=uuid4())
        assert order.status == after
        assert db.add.call_count == 1
        assert db.add.call_args.args[0].payload == {"from": before.value, "to": after.value, "reason": "TEST"}
    else:
        with pytest.raises(InvalidTransitionError):
            transition(order, after, db=db)
        assert order.status == before
        db.add.assert_not_called()


def test_status_assignments_only_in_state_machine():
    for directory in ["app/services", "app/workers", "app/api"]:
        for path in Path(directory).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        assert not (
                            isinstance(target, ast.Attribute)
                            and target.attr == "status"
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "order"
                        ), f"Direct order status assignment at {path}:{node.lineno}"
