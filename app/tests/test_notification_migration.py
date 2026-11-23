import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.models.entities import DEFAULT_TENANT_ID, Notification


def roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = sa.create_engine(url, hide_parameters=True)
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "0019_outbound_webhooks")
        orders = sa.Table(
            "orders",
            sa.MetaData(),
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("tenant_id", sa.Uuid()),
            autoload_with=engine,
        )
        identity = uuid4()
        with engine.begin() as conn:
            conn.execute(
                orders.insert().values(
                    id=identity,
                    tenant_id=DEFAULT_TENANT_ID,
                    principal_scope="tenant",
                    idempotency_key="notify-legacy",
                    status="CREATED",
                    total_amount=10,
                    currency="INR",
                )
            )
        command.upgrade(config, "head")
        command.downgrade(config, "0019_outbound_webhooks")
        command.upgrade(config, "head")
        with Session(engine) as db:
            row = Notification(
                tenant_id=DEFAULT_TENANT_ID, order_id=identity, channel="console", template="order_confirmed"
            )
            db.add(row)
            db.commit()
            notification_id = row.id
        with pytest.raises(RuntimeError, match="operational notifications"):
            command.downgrade(config, "0019_outbound_webhooks")
        with Session(engine) as db:
            assert db.get(Notification, notification_id).status == "pending"
        print("NOTIFICATION LEGACY ROUNDTRIP PASS; OPERATIONAL JOURNAL/SETTING DOWNGRADE REFUSED")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_notification_migration(tmp_path, monkeypatch):
    roundtrip(f"sqlite:///{tmp_path / 'notification.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_notification_migration(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    engine = sa.create_engine(url)
    schema = "notification_" + uuid4().hex
    with engine.begin() as conn:
        conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
    try:
        scoped = sa.engine.make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
        roundtrip(scoped.render_as_string(hide_password=False), monkeypatch)
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()
