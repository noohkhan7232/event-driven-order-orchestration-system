from pathlib import Path
from uuid import UUID

from conftest import TestingSessionLocal
from test_outbound_webhooks import seller_endpoint as seller_endpoint
from test_payment_config import payment_settings as payment_settings
from test_webhook_delivery import due, ping

from app.core.config import Settings
from app.models.entities import WebhookDelivery, WebhookEndpoint
from app.services import webhook_delivery


def test_disable_after_failures_explicitly_documents_consecutive_attempts():
    description = Settings.model_fields["webhook_disable_after_failures"].description or ""
    assert "consecutive failed attempts" in description.lower()
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "# Consecutive failed attempts" in env


def test_two_failed_attempts_of_one_delivery_disable_endpoint_before_delivery_exhaustion(
    db, client, seller_endpoint, monkeypatch
):
    _, auth, endpoint, settings = seller_endpoint
    settings.webhook_disable_after_failures = 2
    settings.webhook_max_attempts = 4
    identity = ping(client, auth, endpoint)
    monkeypatch.setattr(webhook_delivery, "send_webhook", lambda *args: 503)
    assert webhook_delivery.run_delivery_batch(TestingSessionLocal, settings) == {"failed": 1}
    db.expire_all()
    config = db.get(WebhookEndpoint, UUID(endpoint["id"]))
    assert config.consecutive_failures == 1 and config.is_active
    due(db, identity)
    assert webhook_delivery.run_delivery_batch(TestingSessionLocal, settings) == {"failed": 1}
    db.expire_all()
    row = db.get(WebhookDelivery, identity)
    assert row.status == "failed" and row.attempts == 2
    assert config.consecutive_failures == 2 and not config.is_active and config.disabled_at is not None
