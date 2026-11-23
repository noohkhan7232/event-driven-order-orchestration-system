import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from legacy_orders import seed_legacy_order
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.models.entities import DEFAULT_TENANT_ID
from app.models.enums import OrderStatus


def shipping_roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = sa.create_engine(url, hide_parameters=True)
    config = Config("alembic.ini")
    identity = uuid4()
    try:
        command.upgrade(config, "0017_payment_polling")
        legacy = sa.Table(
            "shipments",
            sa.MetaData(),
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("order_id", sa.Uuid()),
            autoload_with=engine,
        )
        with Session(engine) as db:
            order = seed_legacy_order(
                db,
                tenant_id=DEFAULT_TENANT_ID,
                principal_scope="tenant",
                idempotency_key="shipping-legacy",
                status=OrderStatus.FULFILLMENT_REQUESTED,
                total_amount=59.97,
            )
            db.flush()
            db.execute(
                legacy.insert().values(
                    id=identity,
                    order_id=order.id,
                    carrier="c" * 80,
                    tracking_number="t" * 120,
                    status="CREATED",
                    provider="mock",
                )
            )
            db.commit()
        command.upgrade(config, "head")
        command.downgrade(config, "0017_payment_polling")
        command.upgrade(config, "head")
        with engine.begin() as conn:
            row = conn.execute(
                sa.text("SELECT carrier,tracking_number,status,tracking_url,estimated_delivery FROM shipments")
            ).one()
            assert row.carrier == "c" * 80 and row.tracking_number == "t" * 120 and row.status == "CREATED"
            assert row.tracking_url is None and row.estimated_delivery is None
            assert conn.scalar(sa.text("SELECT shipping_provider FROM tenants")) == "mock"
            conn.execute(sa.text("UPDATE shipments SET tracking_url='https://example.com/legacy'"))
        with pytest.raises(RuntimeError, match="operational manual shipping"):
            command.downgrade(config, "0017_payment_polling")
        with engine.connect() as conn:
            assert conn.scalar(sa.text("SELECT tracking_url FROM shipments")) == "https://example.com/legacy"
            assert str(conn.scalar(sa.text("SELECT total_amount FROM orders"))) == "59.97"
        print(
            "SHIPPING LEGACY ROUNDTRIP: IDS/80-CHAR CARRIER/120-CHAR TRACKING/MONEY PRESERVED; "
            "OPERATIONAL DOWNGRADE REFUSED"
        )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_shipping_migration(tmp_path, monkeypatch):
    shipping_roundtrip(f"sqlite:///{tmp_path / 'shipping.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_shipping_migration(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    schema = "shipping_" + uuid4().hex
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
    try:
        scoped = sa.engine.make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
        shipping_roundtrip(scoped.render_as_string(hide_password=False), monkeypatch)
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()
