import pytest

from app.events.contracts import DomainEvent, EventNames
from app.models.entities import Payment, Shipment
from app.models.enums import OrderStatus
from app.workers import tasks


def drain_financial(order):
    from sqlalchemy import select
    from sqlalchemy.orm import object_session

    from app.models.entities import InboundWebhookEvent, OutboxEvent, Refund

    db = object_session(order)
    references = {payment.provider_reference for payment in order.payments if payment.provider == "mock"}
    receipts = list(
        db.scalars(
            select(InboundWebhookEvent).where(
                InboundWebhookEvent.provider == "mock",
                InboundWebhookEvent.tenant_id == order.tenant_id,
                InboundWebhookEvent.status == "received",
            )
        )
    )
    for receipt in receipts:
        if any(fields["provider_order_id"] in references for fields in receipt.payload["events"]):
            row = db.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == receipt.id, OutboxEvent.event_type == "payment.provider_event"
                )
            )
            tasks.process_event.run(row.payload)
    for refund in list(db.scalars(select(Refund).where(Refund.order_id == order.id, Refund.status == "requested"))):
        tasks.process_refund.run(str(refund.id))


def consume(order, kind, event=None):
    event = event or DomainEvent(event_type=kind, aggregate_id=order.id, payload={}, correlation_id="flow-trace")
    tasks.process_event.run(event.model_dump(mode="json"))
    drain_financial(order)
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
    assert (
        db.query(ProcessedEvent).count()
        == db.query(OutboxEvent).filter(OutboxEvent.event_type != EventNames.ORDER_STATUS_CHANGED).count()
        == 4
    )
    assert db.query(ProcessedEvent).filter_by(consumer="payment_provider").count() == 1
    assert db.query(OutboxEvent).filter_by(event_type="payment.provider_event").count() == 1
    assert db.query(OutboxEvent).filter_by(event_type=EventNames.ORDER_STATUS_CHANGED).count() == 4
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
