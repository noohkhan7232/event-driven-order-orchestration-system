import smtplib
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from conftest import TestingSessionLocal
from sqlalchemy import select

from app.core.config import get_settings
from app.events.contracts import DomainEvent
from app.models.entities import Notification, Order, Tenant
from app.notifications.service import queue_notifications, run_notifications


@pytest.mark.postgres
def test_smtp_in_flight_has_no_order_tenant_notification_locks(db, workflow, monkeypatch):
    order, _ = workflow
    order.buyer_email = "private@example.com"
    monkeypatch.setattr(get_settings(), "notifications_backend", "smtp")
    queue_notifications(
        db, order, DomainEvent(event_type="order.status_changed", aggregate_id=order.id, payload={"to": "paid"})
    )
    db.commit()
    identity = db.scalar(select(Notification.id))
    db.commit()
    entered, release = Event(), Event()
    sent = []

    class SMTP:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def ehlo(self):
            pass

        def send_message(self, message):
            sent.append(message["Message-ID"])
            entered.set()
            assert release.wait(15)
            return {}

    monkeypatch.setattr(smtplib, "SMTP", SMTP)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_notifications, TestingSessionLocal, get_settings())
        try:
            assert entered.wait(15)
            assert run_notifications(TestingSessionLocal, get_settings()) == {}
            with TestingSessionLocal() as observer:
                for column, value in ((Notification.id, identity), (Order.id, order.id), (Tenant.id, order.tenant_id)):
                    assert observer.scalar(select(column).where(column == value).with_for_update(nowait=True)) == value
        finally:
            release.set()
        assert future.result(timeout=15) == {"sent": 1}
    db.expire_all()
    assert db.get(Notification, identity).attempts == 1 and len(sent) == 1
    print("SMTP IN FLIGHT: ORDER/TENANT/NOTIFICATION NOWAIT PASS; PEER LEASE SKIP; ONE SEND")
