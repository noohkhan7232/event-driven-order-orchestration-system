"""PII-allowlisted envelopes and tenant-scoped idempotent fan-out."""

from uuid import UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.events.contracts import DomainEvent
from app.models.entities import Order, WebhookDelivery, WebhookEndpoint
from app.services.payment_timing import database_now


def enqueue(db: Session, endpoint: WebhookEndpoint, identity: UUID, kind: str, payload: dict) -> WebhookDelivery:
    if db.get_bind().dialect.name == "sqlite":
        connection = db.connection()
        if not getattr(connection.connection.driver_connection, "in_transaction", False):
            connection.exec_driver_sql("BEGIN")
    try:
        with db.begin_nested():
            row = WebhookDelivery(
                tenant_id=endpoint.tenant_id,
                endpoint_id=endpoint.id,
                event_id=identity,
                event_type=kind,
                payload=payload,
            )
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        existing = db.scalar(
            select(WebhookDelivery).where(
                WebhookDelivery.endpoint_id == endpoint.id,
                WebhookDelivery.tenant_id == endpoint.tenant_id,
                WebhookDelivery.event_id == identity,
            )
        )
        if existing is None:
            raise
        return existing


def queue_ping(db: Session, endpoint: WebhookEndpoint) -> WebhookDelivery:
    identity = uuid4()
    now = database_now(db)
    return enqueue(
        db,
        endpoint,
        identity,
        "ping",
        {
            "id": str(identity),
            "type": "ping",
            "created_at": now.isoformat(),
            "tenant_id": str(endpoint.tenant_id),
            "data": {"message": "ping"},
        },
    )


def fanout(db: Session, order: Order, event: DomainEvent) -> None:
    if event.tenant_id is not None and event.tenant_id != order.tenant_id:
        return
    if event.event_type == "order.status_changed":
        kinds = ["order.status_changed"]
        alias = {
            "paid": "order.paid",
            "shipped": "order.shipped",
            "delivered": "order.delivered",
            "failed": "order.failed",
            "cancelled": "order.cancelled",
        }.get(str(event.payload.get("to")))
        if alias:
            kinds.append(alias)
        status = event.payload.get("to", order.status.value)
    elif event.event_type in {"order.created", "refund.succeeded"}:
        kinds = ["order.created" if event.event_type == "order.created" else "payment.refunded"]
        status = "created" if event.event_type == "order.created" else order.status.value
    else:
        return
    endpoints = list(
        db.scalars(
            select(WebhookEndpoint).where(
                WebhookEndpoint.tenant_id == order.tenant_id,
                WebhookEndpoint.is_active.is_(True),
                WebhookEndpoint.deleted_at.is_(None),
            )
        )
    )
    if not endpoints:
        return
    db.flush()
    # Never serialize OrderRead, customer, buyer fields, reasons or shipment notes.
    data = {
        "order_id": str(order.id),
        "external_reference": order.external_reference,
        "status": status,
        "from": event.payload.get("from"),
        "to": event.payload.get("to"),
        "currency": order.currency,
        "total": str(order.total_amount),
        "items": [{"sku": item.sku, "name": item.product_name, "quantity": item.quantity} for item in order.items],
        "shipments": [{"carrier": row.carrier, "tracking_number": row.tracking_number} for row in order.shipments],
        "created_at": order.created_at.isoformat(),
        "updated_at": order.updated_at.isoformat(),
    }
    for kind in kinds:
        # Each semantic alias is a distinct logical event. Subscribing to both
        # alias and generic status must not collide on (endpoint_id,event_id).
        identity = event.event_id if kind == kinds[0] else uuid5(event.event_id, kind)
        payload = {
            "id": str(identity),
            "type": kind,
            "created_at": event.occurred_at.isoformat(),
            "tenant_id": str(order.tenant_id),
            "data": data,
        }
        for endpoint in endpoints:
            if kind in endpoint.events:
                enqueue(db, endpoint, identity, kind, payload)
