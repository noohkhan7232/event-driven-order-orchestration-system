import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.models.entities import DEFAULT_TENANT_ID, InboundWebhookEvent, OutboxEvent
from app.services.inbound_payments import stage_receipt


def roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = sa.create_engine(url, hide_parameters=True)
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "0011_sessions")
        command.upgrade(config, "head")
        assert "inbound_webhook_events" in sa.inspect(engine).get_table_names()
        command.downgrade(config, "0011_sessions")
        command.upgrade(config, "head")
        with Session(engine) as db:
            stage_receipt(db, "mock", DEFAULT_TENANT_ID, "evt_migration", [])
            db.commit()
        with pytest.raises(RuntimeError, match="receipts require explicit archival"):
            command.downgrade(config, "0011_sessions")
        with Session(engine) as db:
            assert db.query(InboundWebhookEvent).count() == 1
            assert db.query(OutboxEvent).filter_by(event_type="payment.provider_event").count() == 1
        print("INBOUND EMPTY ROUNDTRIP PASS; OPERATIONAL RECEIPT AND INTENT PRESERVED ON REFUSAL")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_inbound_migration(tmp_path, monkeypatch):
    roundtrip(f"sqlite:///{tmp_path / 'inbound.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_inbound_migration(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    schema = "inbound_" + uuid4().hex
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(sa.text(f"CREATE SCHEMA {schema}"))
    try:
        scoped = sa.engine.make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
        roundtrip(scoped.render_as_string(hide_password=False), monkeypatch)
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()
