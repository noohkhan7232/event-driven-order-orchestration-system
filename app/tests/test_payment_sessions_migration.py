import os
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from app.core.config import get_settings
from app.models.entities import DEFAULT_TENANT_ID


def session_roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = sa.create_engine(url, hide_parameters=True)
    config = Config("alembic.ini")
    identity = uuid4()
    try:
        command.upgrade(config, "0010_payment_config")
        legacy = sa.Table(
            "orders",
            sa.MetaData(),
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("tenant_id", sa.Uuid()),
            autoload_with=engine,
        )
        with engine.begin() as conn:
            conn.execute(
                legacy.insert().values(
                    id=identity,
                    tenant_id=DEFAULT_TENANT_ID,
                    principal_scope="tenant",
                    status="CREATED",
                    idempotency_key="legacy",
                    inventory_reserved=False,
                    total_amount=19.99,
                    currency="INR",
                )
            )
        command.upgrade(config, "head")
        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT payment_method,payment_expires_at,total_amount FROM orders")).one()
            assert row.payment_method == "prepaid" and row.payment_expires_at is None
            assert Decimal(str(row.total_amount)) == Decimal("19.99")
        command.downgrade(config, "0010_payment_config")
        command.upgrade(config, "head")
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE orders SET payment_method='cod'"))
        with pytest.raises(RuntimeError, match="operational payment session"):
            command.downgrade(config, "0010_payment_config")
        with engine.connect() as conn:
            assert conn.scalar(sa.text("SELECT payment_method FROM orders")) == "cod"
        print("SESSION LEGACY DEFAULT/BACKWARD ROUNDTRIP PASS; OPERATIONAL DOWNGRADE REFUSED")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_session_migration(tmp_path, monkeypatch):
    session_roundtrip(f"sqlite:///{tmp_path / 'sessions.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_session_migration(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    schema = "session_" + uuid4().hex
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
    try:
        scoped = sa.engine.make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
        session_roundtrip(scoped.render_as_string(hide_password=False), monkeypatch)
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()
