import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.models.entities import DEFAULT_TENANT_ID, Order, User
from app.models.enums import OrderStatus, UserRole


def legacy_timeline_roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    engine = sa.create_engine(url, hide_parameters=True)
    try:
        command.upgrade(config, "0008_key_prefix")
        with Session(engine) as db:
            user = User(
                tenant_id=DEFAULT_TENANT_ID,
                email="timeline-legacy@example.com",
                full_name="Legacy",
                hashed_password="hash",
                role=UserRole.CUSTOMER,
            )
            db.add(user)
            db.flush()
            order = Order(
                tenant_id=DEFAULT_TENANT_ID,
                customer_id=user.id,
                principal_scope=f"user:{user.id}",
                idempotency_key="legacy-timeline",
                status=OrderStatus.PAID,
                total_amount=19.99,
                currency="INR",
            )
            db.add(order)
            db.commit()
        command.upgrade(config, "head")
        assert sa.inspect(engine).has_table("order_timeline")
        with engine.connect() as conn:
            row = conn.execute(sa.text("SELECT from_status,to_status,reason FROM order_timeline")).one()
            assert row.from_status is None and row.to_status == "paid" and row.reason == "MIGRATION_BACKFILL"
        indexes = sa.inspect(engine).get_indexes("order_timeline")
        assert {tuple(index["column_names"]) for index in indexes} >= {("tenant_id", "id"), ("order_id", "id")}
        command.downgrade(config, "0008_key_prefix")
        with engine.connect() as conn:
            assert conn.scalar(sa.text("SELECT COUNT(*) FROM orders")) == 1
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert conn.scalar(sa.text("SELECT COUNT(*) FROM order_timeline")) == 1
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO order_timeline (tenant_id,order_id,from_status,to_status) "
                    "SELECT tenant_id,id,'paid','fulfillment_requested' FROM orders"
                )
            )
        with pytest.raises(RuntimeError, match="operational order timeline history"):
            command.downgrade(config, "0008_key_prefix")
        with engine.connect() as conn:
            assert conn.scalar(sa.text("SELECT COUNT(*) FROM order_timeline")) == 2
        print("TIMELINE LEGACY BACKFILL AND 0008/HEAD ROUNDTRIP PASS; ORDER PRESERVED")
        print("OPERATIONAL TIMELINE DOWNGRADE REFUSAL PASS; HISTORY RETAINED")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_timeline_backfill_roundtrip(tmp_path, monkeypatch):
    legacy_timeline_roundtrip(f"sqlite:///{tmp_path / 'timeline.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_timeline_backfill_roundtrip(monkeypatch):
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["TEST_DATABASE_URL"])
    engine = sa.create_engine(url)
    schema = "timeline_" + uuid4().hex
    with engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    try:
        legacy_timeline_roundtrip(
            url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False),
            monkeypatch,
        )
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()
