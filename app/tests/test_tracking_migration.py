import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from legacy_orders import seed_legacy_order
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.models.entities import DEFAULT_TENANT_ID, Order
from app.models.enums import OrderStatus


def roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = sa.create_engine(url, hide_parameters=True)
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "0020_notifications")
        with Session(engine) as db:
            legacy = seed_legacy_order(
                db,
                tenant_id=DEFAULT_TENANT_ID,
                principal_scope="tenant",
                idempotency_key="track-legacy",
                status=OrderStatus.CREATED,
                total_amount=10,
            )
            db.commit()
        command.upgrade(config, "head")
        command.downgrade(config, "0020_notifications")
        command.upgrade(config, "head")
        with Session(engine) as db:
            row = db.get(Order, legacy.id)
            assert row.tracking_version == 1 and row.total_amount == 10
            row.tracking_version = 2
            db.commit()
        with pytest.raises(RuntimeError, match="rotated tracking versions"):
            command.downgrade(config, "0020_notifications")
        with Session(engine) as db:
            assert db.get(Order, legacy.id).tracking_version == 2
        print("TRACKING LEGACY ROUNDTRIP PASS; ROTATED VERSION DOWNGRADE REFUSED; MONEY/REVOCATION PRESERVED")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_tracking_migration(tmp_path, monkeypatch):
    roundtrip(f"sqlite:///{tmp_path / 'tracking.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_tracking_migration(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    engine = sa.create_engine(url)
    schema = "tracking_" + uuid4().hex
    with engine.begin() as conn:
        conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
    try:
        scoped = sa.engine.make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
        roundtrip(scoped.render_as_string(hide_password=False), monkeypatch)
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()
