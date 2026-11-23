"""Stateless revocable tracking tokens and an explicit public projection."""

import base64
import hashlib
import hmac
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.models.entities import Order, OrderTimeline, Tenant
from app.repositories.orders import OrderRepository

PUBLIC_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
FRIENDLY = {
    "created": "confirmed",
    "validated": "confirmed",
    "inventory_reserved": "processing",
    "payment_pending": "processing",
    "paid": "processing",
    "fulfillment_requested": "processing",
    "shipped": "shipped",
    "delivered": "delivered",
    "cancelled": "cancelled",
    "failed": "failed",
}
LABELS = {
    "created": "Order received",
    "validated": "Order confirmed",
    "inventory_reserved": "Preparing order",
    "payment_pending": "Awaiting payment",
    "paid": "Payment received",
    "fulfillment_requested": "Preparing shipment",
    "shipped": "Shipped",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
    "failed": "Unable to complete order",
}


class TrackingNotFound(NotFoundError):
    headers = PUBLIC_HEADERS

    def __init__(self):
        super().__init__("Tracking information not found")


def signature(identity: UUID, version: int, settings: Settings) -> str:
    digest = hmac.new(
        settings.tracking_token_secret.get_secret_value().encode(), f"{identity}:{version}".encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def make_token(identity: UUID, version: int, settings: Settings) -> str:
    return identity.hex + "." + signature(identity, version, settings)


def tracked_order(db: Session, token: str, settings: Settings) -> tuple[Order, Tenant]:
    shaped = re.fullmatch(r"[0-9a-f]{32}\.[A-Za-z0-9_-]{43}", token) is not None
    identity = UUID(hex=token[:32]) if shaped else UUID(int=0)
    candidate = db.execute(
        select(Order, Tenant).join(Tenant, Tenant.id == Order.tenant_id).where(Order.id == identity)
    ).first()
    version = candidate[0].tracking_version if candidate else 1
    expected = signature(identity, version, settings)
    provided = token[33:] if shaped else "0" * 43
    matches = hmac.compare_digest(expected, provided)
    if not shaped or not matches or candidate is None or candidate[1].status != "active":
        raise TrackingNotFound()
    order = OrderRepository(db).get_with_details(identity, tenant_id=candidate[1].id)
    if order is None:
        raise TrackingNotFound()
    return order, candidate[1]


def public_projection(db: Session, order: Order, tenant: Tenant, settings: Settings) -> dict:
    timeline = db.scalars(
        select(OrderTimeline)
        .where(OrderTimeline.order_id == order.id, OrderTimeline.tenant_id == tenant.id)
        .order_by(OrderTimeline.id)
    )
    result = {
        "store": tenant.name,
        "status": FRIENDLY[order.status.value],
        "timeline": [
            {
                "at": row.created_at,
                "label": LABELS.get(row.to_status, "Order updated"),
                "status": FRIENDLY.get(row.to_status, "processing"),
            }
            for row in timeline
        ],
        "items": [{"name": row.product_name, "quantity": row.quantity} for row in order.items],
        "shipments": [
            {
                "carrier": row.carrier,
                "tracking_number": row.tracking_number,
                "tracking_url": row.tracking_url,
                "estimated_delivery": row.estimated_delivery
                or (row.estimated_delivery_at.date() if row.estimated_delivery_at else None),
                "events": [
                    {"status": event.status.value, "at": event.created_at}
                    for event in sorted(row.events, key=lambda event: (event.created_at, event.id))
                ],
            }
            for row in order.shipments
        ],
    }
    if settings.public_tracking_show_total:
        result["total"] = f"{order.total_amount:.2f}"
        result["currency"] = order.currency
    return result
