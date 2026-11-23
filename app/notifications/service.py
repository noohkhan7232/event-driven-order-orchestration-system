from dataclasses import dataclass
from datetime import timedelta
from html import escape
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.events.contracts import DomainEvent
from app.models.entities import Notification, Order, Tenant
from app.notifications.channels import (
    Message,
    PermanentNotification,
    TransientNotification,
    UnknownNotification,
    get_channel,
)
from app.services.payment_timing import database_now

LABELS = {
    "order_confirmed": "Order confirmed",
    "order_shipped": "Order shipped",
    "order_delivered": "Order delivered",
    "order_failed": "Order could not be completed",
    "order_cancelled": "Order cancelled",
    "refund_processed": "Refund processed",
}


def queue_notifications(db: Session, order: Order, event: DomainEvent) -> None:
    if event.tenant_id is not None and event.tenant_id != order.tenant_id:
        return
    to = event.payload.get("to")
    template = None
    if event.event_type == "refund.succeeded":
        template = "refund_processed"
    elif event.event_type == "order.status_changed":
        template = {
            "shipped": "order_shipped",
            "delivered": "order_delivered",
            "failed": "order_failed",
            "cancelled": "order_cancelled",
        }.get(str(to))
        if (order.payment_method == "prepaid" and to == "paid") or (
            order.payment_method == "cod" and to == "fulfillment_requested"
        ):
            template = "order_confirmed"
    if template is None:
        return
    channel = get_settings().notifications_backend
    if db.get_bind().dialect.name == "sqlite":
        connection = db.connection()
        if not getattr(connection.connection.driver_connection, "in_transaction", False):
            connection.exec_driver_sql("BEGIN")
    try:
        with db.begin_nested():
            db.add(Notification(tenant_id=order.tenant_id, order_id=order.id, channel=channel, template=template))
            db.flush()
    except IntegrityError:
        if (
            db.scalar(
                select(Notification.id).where(
                    Notification.order_id == order.id,
                    Notification.channel == channel,
                    Notification.template == template,
                )
            )
            is None
        ):
            raise


@dataclass(frozen=True)
class Claim:
    identity: UUID
    token: UUID
    channel: str
    message: Message


def claim(factory: sessionmaker[Session], settings: Settings) -> Claim | str | None:
    with factory() as db:
        now = database_now(db)
        row = db.scalar(
            select(Notification)
            .where(
                Notification.status == "pending",
                Notification.next_attempt_at <= now,
                or_(
                    Notification.claim_token.is_(None),
                    Notification.lease_until.is_(None),
                    Notification.lease_until <= now,
                ),
            )
            .order_by(Notification.next_attempt_at, Notification.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            db.rollback()
            return None
        if row.claim_token:
            from app.auth.principal import aware

            if row.lease_until and aware(row.lease_until) > now:
                db.rollback()
                return None
            # SMTP has no remote idempotency/lookup contract. Do not resend an
            # uncertain crash outcome and risk duplicate buyer email.
            row.status, row.last_error = "failed", "send_outcome_unknown"
            row.claim_token = row.lease_until = None
            db.commit()
            return "failed"
        tenant = db.get(Tenant, row.tenant_id)
        order = db.scalar(select(Order).where(Order.id == row.order_id, Order.tenant_id == row.tenant_id))
        if (
            tenant is None
            or tenant.status != "active"
            or not tenant.notifications_enabled
            or order is None
            or not order.buyer_email
        ):
            row.status = "skipped"
            db.commit()
            return "skipped"
        recipient = order.buyer_email
        local, _, domain = recipient.partition("@")
        row.recipient_hint = (local[:1] + "***@" + domain)[:255]
        row.attempts += 1
        row.claim_token, row.lease_until = uuid4(), now + timedelta(seconds=settings.notification_lease_seconds)
        name = " ".join(tenant.name.splitlines())
        label = LABELS[row.template]
        text = f"{name}\n{label}\nOrder {order.id}\n"
        html = f"<h1>{escape(name)}</h1><p>{escape(label)}</p><p>Order {order.id}</p>"
        result = Claim(
            row.id,
            row.claim_token,
            row.channel,
            Message(str(row.id), row.template, recipient, f"{name}: {label}", text, html),
        )
        db.commit()
        return result


def run_notifications(factory: sessionmaker[Session], settings: Settings) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _ in range(settings.notification_batch_size):
        item = claim(factory, settings)
        if item is None:
            break
        if isinstance(item, str):
            status = item
        else:
            error = None
            transient = False
            try:
                get_channel(item.channel, settings).send(item.message)
            except TransientNotification:
                error, transient = "smtp_transient", True
            except UnknownNotification:
                error = "send_outcome_unknown"
            except PermanentNotification:
                error = "smtp_permanent"
            with factory() as db:
                row = db.scalar(select(Notification).where(Notification.id == item.identity).with_for_update())
                if row is None or row.claim_token != item.token:
                    db.rollback()
                    continue
                row.status = (
                    "sent"
                    if error is None
                    else "pending"
                    if transient and row.attempts < settings.notification_max_attempts
                    else "failed"
                )
                row.last_error = error
                row.claim_token = row.lease_until = None
                row.next_attempt_at = database_now(db) + timedelta(
                    seconds=min(3600, settings.notification_retry_seconds * 2 ** min(row.attempts - 1, 20))
                )
                status = row.status
                db.commit()
        counts[status] = counts.get(status, 0) + 1
    return counts
