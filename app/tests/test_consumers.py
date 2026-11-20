import pytest

from app.events.contracts import DomainEvent, EventNames
from app.models.entities import Payment, Shipment
from app.models.enums import OrderStatus
from app.workers import tasks


def consume(order, kind, event=None):
    event = event or DomainEvent(event_type=kind, aggregate_id=order.id, payload={}, correlation_id="flow-trace")
    tasks.process_event.run(event.model_dump(mode="json"))
    return event


def test_duplicate_redelivery_has_one_effect_per_step(db, workflow):
    from app.models.entities import OutboxEvent, ProcessedEvent

    order, stock = workflow
    for kind in [EventNames.ORDER_CREATED, EventNames.INVENTORY_RESERVED, EventNames.PAYMENT_AUTHORIZED]:
        event = consume(order, kind)
        consume(order, kind, event)
    assert order.status == OrderStatus.FULFILLMENT_REQUESTED
    assert stock.available_quantity == 9 and stock.reserved_quantity == 1
    assert db.query(Payment).count() == db.query(Shipment).count() == 1
    assert db.query(ProcessedEvent).count() == db.query(OutboxEvent).count() == 3
    assert {row.correlation_id for row in db.query(OutboxEvent)} == {"flow-trace"}


def test_stale_events_are_recorded_without_effects(db, workflow):
    from app.models.entities import ProcessedEvent

    order, stock = workflow
    consume(order, EventNames.PAYMENT_AUTHORIZED)
    consume(order, EventNames.INVENTORY_RESERVED)
    consume(order, "shipment.created")
    assert order.status == OrderStatus.CREATED
    assert stock.available_quantity == 10
    assert db.query(Payment).count() == db.query(Shipment).count() == 0
    assert db.query(ProcessedEvent).count() == 3


def test_technical_error_rolls_back_marker_stock_and_next_event(db, workflow, monkeypatch):
    from app.core.exceptions import TransientError
    from app.models.entities import OutboxEvent, ProcessedEvent
    from app.repositories.outbox import OutboxRepository

    order, stock = workflow
    event = DomainEvent(event_type=EventNames.ORDER_CREATED, aggregate_id=order.id, payload={})
    original = OutboxRepository.add
    monkeypatch.setattr(OutboxRepository, "add", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("technical")))
    with pytest.raises(TransientError):
        consume(order, event.event_type, event)
    db.expire_all()
    assert order.status == OrderStatus.CREATED
    assert stock.available_quantity == 10
    assert db.query(ProcessedEvent).count() == db.query(OutboxEvent).count() == 0
    db.rollback()
    monkeypatch.setattr(OutboxRepository, "add", original)
    consume(order, event.event_type, event)
    assert stock.available_quantity == 9
    assert db.query(ProcessedEvent).count() == 1
