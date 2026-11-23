from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import TestingSessionLocal
from test_payment_config import identity

from app.core.config import get_settings
from app.events.contracts import DomainEvent
from app.models.entities import Tenant


@pytest.mark.parametrize(
    "method,to,template",
    [
        ("prepaid", "paid", "order_confirmed"),
        ("cod", "fulfillment_requested", "order_confirmed"),
        ("prepaid", "shipped", "order_shipped"),
        ("prepaid", "delivered", "order_delivered"),
        ("prepaid", "failed", "order_failed"),
        ("prepaid", "cancelled", "order_cancelled"),
        ("prepaid", "refund", "refund_processed"),
    ],
)
def test_notification_triggers_once_and_sends_without_transaction(
    db, workflow, monkeypatch, method, to, template, caplog
):
    from app.models.entities import Notification
    from app.notifications.service import queue_notifications, run_notifications

    order, _ = workflow
    order.payment_method, order.buyer_email = method, "buyer-private@example.com"
    db.commit()
    event = DomainEvent(
        event_type="refund.succeeded" if to == "refund" else "order.status_changed",
        aggregate_id=order.id,
        tenant_id=order.tenant_id,
        payload={"to": to},
    )
    for _ in range(2):
        queue_notifications(db, order, event)
        db.commit()
    row = db.query(Notification).one()
    assert row.template == template and row.recipient_hint is None
    db.commit()
    sent = []

    class Channel:
        def send(self, message):
            assert not db.in_transaction()
            sent.append(message)

    monkeypatch.setattr("app.notifications.service.get_channel", lambda *a: Channel())
    run_notifications(TestingSessionLocal, get_settings())
    run_notifications(TestingSessionLocal, get_settings())
    db.expire_all()
    assert row.status == "sent" and row.attempts == 1 and len(sent) == 1
    assert row.recipient_hint == "b***@example.com"
    assert "buyer-private" not in repr(event.payload) + caplog.text


def test_notification_settings_optout_and_missing_recipient(db, client, workflow, monkeypatch):
    from app.models.entities import Notification
    from app.notifications.service import queue_notifications, run_notifications

    order, _ = workflow
    _, auth = identity(db)
    assert client.get("/api/v1/settings/notifications", headers=auth).json() == {"notifications_enabled": True}
    assert (
        client.patch("/api/v1/settings/notifications", headers=auth, json={"notifications_enabled": False}).status_code
        == 200
    )
    event = DomainEvent(
        event_type="order.status_changed", aggregate_id=order.id, tenant_id=order.tenant_id, payload={"to": "paid"}
    )
    queue_notifications(db, order, event)
    db.commit()
    monkeypatch.setattr("app.notifications.service.get_channel", lambda *a: pytest.fail("Skipped notification sent"))
    run_notifications(TestingSessionLocal, get_settings())
    db.expire_all()
    assert db.query(Notification).one().status == "skipped"
    assert (
        client.patch("/api/v1/settings/notifications", headers=auth, json={"notifications_enabled": True}).status_code
        == 200
    )
    event = event.model_copy(update={"event_id": uuid4(), "payload": {"to": "shipped"}})
    queue_notifications(db, order, event)
    db.commit()
    run_notifications(TestingSessionLocal, get_settings())
    db.expire_all()
    assert {row.status for row in db.query(Notification)} == {"skipped"}


def test_transient_permanent_crash_budget_and_html_escaping(db, workflow, monkeypatch):
    from app.models.entities import Notification
    from app.notifications.channels import PermanentNotification, TransientNotification
    from app.notifications.service import queue_notifications, run_notifications

    order, _ = workflow
    order.buyer_email = "recipient@example.com"
    db.get(Tenant, order.tenant_id).name = "<script>{{7*7}}</script>"
    db.commit()
    settings = get_settings().model_copy(update={"notification_retry_seconds": 1})
    event = DomainEvent(event_type="order.status_changed", aggregate_id=order.id, payload={"to": "paid"})
    queue_notifications(db, order, event)
    db.commit()
    messages = []

    class Channel:
        def send(self, message):
            messages.append(message)
            if len(messages) == 1:
                raise TransientNotification()

    monkeypatch.setattr("app.notifications.service.get_channel", lambda *a: Channel())
    run_notifications(TestingSessionLocal, settings)
    db.expire_all()
    row = db.query(Notification).one()
    assert row.status == "pending" and row.last_error == "smtp_transient"
    row.next_attempt_at -= timedelta(seconds=10)
    db.commit()
    run_notifications(TestingSessionLocal, settings)
    db.expire_all()
    assert row.status == "sent" and row.attempts == 2
    assert "<script>" not in messages[-1].html and "&lt;script&gt;{{7*7}}" in messages[-1].html
    assert "{{7*7}}" in messages[-1].text
    monkeypatch.setattr(Channel, "send", lambda *a: (_ for _ in ()).throw(PermanentNotification()))
    queue_notifications(db, order, event.model_copy(update={"payload": {"to": "failed"}}))
    db.commit()
    run_notifications(TestingSessionLocal, settings)
    run_notifications(TestingSessionLocal, settings)
    db.expire_all()
    assert db.query(Notification).filter_by(template="order_failed").one().status == "failed"


def test_crash_unknown_outcome_never_resends_and_irrelevant_events_skip(db, workflow, monkeypatch):
    from app.models.entities import Notification
    from app.notifications import service

    order, _ = workflow
    order.buyer_email = "private@example.com"
    db.commit()
    event = DomainEvent(event_type="order.status_changed", aggregate_id=order.id, payload={"to": "paid"})
    service.queue_notifications(db, order, event.model_copy(update={"tenant_id": uuid4()}))
    service.queue_notifications(db, order, event.model_copy(update={"event_type": "irrelevant"}))
    assert db.query(Notification).count() == 0
    service.queue_notifications(db, order, event)
    db.commit()
    claimed = service.claim(TestingSessionLocal, get_settings())
    assert claimed is not None and not isinstance(claimed, str)
    db.expire_all()
    row = db.query(Notification).one()
    row.lease_until -= timedelta(seconds=1000)
    db.commit()
    monkeypatch.setattr(service, "get_channel", lambda *a: pytest.fail("Uncertain outcome retried"))
    assert service.run_notifications(TestingSessionLocal, get_settings()) == {"failed": 1}
    db.expire_all()
    assert row.last_error == "send_outcome_unknown" and row.attempts == 1
