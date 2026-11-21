from uuid import UUID

import pytest
from sqlalchemy import select
from test_order_state import ALLOWED
from test_principal_auth import register_owner

from app.domain.order_state import reopen_failed_order, transition
from app.events.contracts import DomainEvent
from app.models.entities import Order, OutboxEvent
from app.models.enums import OrderStatus
from app.workers import tasks


@pytest.mark.parametrize(
    "before,after",
    [(OrderStatus(before), OrderStatus(after)) for before, values in ALLOWED.items() for after in values],
)
def test_each_valid_transition_records_one_timeline_and_intent(db, workflow, before, after):
    from app.models.entities import OrderTimeline

    order, _ = workflow
    order.status = before
    order.buyer_name, order.buyer_email, order.buyer_phone = (
        "Synthetic Buyer",
        "buyer-private@example.com",
        "+919999999999",
    )
    db.commit()
    transition(order, after, db=db, reason="TEST", correlation_id="timeline-trace")
    db.commit()
    timeline = db.query(OrderTimeline).one()
    event = db.query(OutboxEvent).filter_by(event_type="order.status_changed").one()
    assert (timeline.tenant_id, timeline.order_id, timeline.from_status, timeline.to_status, timeline.reason) == (
        order.tenant_id,
        order.id,
        before.value,
        after.value,
        "TEST",
    )
    assert event.tenant_id == order.tenant_id and event.correlation_id == "timeline-trace"
    assert event.payload["payload"] == {
        "order_id": str(order.id),
        "from": before.value,
        "to": after.value,
        "reason": None,
        "tenant_id": str(order.tenant_id),
    }
    assert all(value not in str(event.payload) for value in [order.buyer_name, order.buyer_email, order.buyer_phone])


def test_transition_rollback_leaves_no_timeline_or_intent(db, workflow):
    from app.models.entities import OrderTimeline

    order, _ = workflow
    transition(order, OrderStatus.INVENTORY_RESERVED, db=db)
    db.flush()
    assert db.query(OrderTimeline).count() == 1
    db.rollback()
    assert db.get(Order, order.id).status == OrderStatus.CREATED
    assert db.query(OrderTimeline).count() == db.query(OutboxEvent).count() == 0


def test_reopen_records_a_fresh_timeline_change(db, workflow):
    from app.models.entities import OrderTimeline

    order, _ = workflow
    order.status, order.failure_reason = OrderStatus.FAILED, "RETRIES_EXHAUSTED"
    db.commit()
    reopen_failed_order(order, db=db, actor=order.customer_id, reason="Operator recovery")
    db.commit()
    row = db.query(OrderTimeline).one()
    assert row.from_status == "failed" and row.to_status == "created"
    event = db.query(OutboxEvent).filter_by(event_type="order.status_changed").one()
    assert row.reason == "Operator recovery" and event.payload["payload"]["reason"] is None


def test_creation_replay_has_one_initial_timeline_row(client, db):
    from app.models.entities import OrderTimeline

    _, owner = register_owner(client)
    client.post("/api/v1/products", headers=owner, json={"sku": "TIMELINE", "name": "Product", "price": "19.99"})
    headers = {**owner, "Idempotency-Key": "timeline-create"}
    body = {"items": [{"sku": "TIMELINE", "quantity": 1}]}
    created = client.post("/api/v1/orders", headers=headers, json=body)
    replay = client.post("/api/v1/orders", headers=headers, json=body)
    assert created.status_code == replay.status_code == 202
    assert created.json()["id"] == replay.json()["id"]
    row = db.query(OrderTimeline).one()
    assert row.order_id == UUID(created.json()["id"]) and row.from_status is None and row.to_status == "created"
    assert db.query(OutboxEvent).filter_by(event_type="order.created").count() == 1


def test_status_changed_consumer_never_transitions_or_emits_again(db, workflow, monkeypatch):
    from app.models.entities import OrderTimeline, ProcessedEvent

    order, _ = workflow

    def forbidden(*args, **kwargs):
        raise AssertionError("Status-change consumers must never call transition")

    monkeypatch.setattr(tasks, "transition", forbidden)
    monkeypatch.setattr("app.domain.order_state.transition", forbidden)
    event = DomainEvent(
        event_type="order.status_changed",
        aggregate_id=order.id,
        tenant_id=order.tenant_id,
        payload={"from": "paid", "to": "delivered"},
    )
    for _ in range(2):
        tasks.process_event.run(event.model_dump(mode="json"))
    assert order.status == OrderStatus.CREATED
    assert db.query(OrderTimeline).count() == db.query(OutboxEvent).count() == 0
    assert db.scalar(select(ProcessedEvent.event_id)) == event.event_id
    assert db.query(ProcessedEvent).count() == 1


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("buyer_name", "Private Buyer", "Requested by PRIVATE BUYER"),
        ("buyer_email", "private@example.com", "Contact private@example.com"),
        ("buyer_phone", "+919999999999", "Call 919999999999"),
    ],
)
def test_status_event_reason_does_not_repeat_buyer_identity(db, workflow, field, value, reason):
    from app.models.entities import OrderTimeline

    order, _ = workflow
    setattr(order, field, value)
    db.commit()
    transition(order, OrderStatus.CANCELLED, db=db, reason=reason)
    db.commit()
    event = db.query(OutboxEvent).filter_by(event_type="order.status_changed").one()
    assert reason not in str(event.payload)
    assert db.query(OrderTimeline).one().reason == reason
