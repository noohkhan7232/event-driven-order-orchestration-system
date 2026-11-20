from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from app.core.config import get_settings


def reflect_legacy_tables(bind):
    metadata = sa.MetaData()
    metadata.reflect(bind)
    # Migration 0001 uses PostgreSQL UUID; SQLite reflects that as NUMERIC.
    # Bind fixture UUIDs explicitly, matching the application's Uuid handling.
    for table in metadata.tables.values():
        for column in table.columns:
            if column.name == "id" or column.name.endswith("_id"):
                column.type = sa.Uuid(native_uuid=False)
    return metadata


def test_legacy_migration_backfill_and_safe_downgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    engine = sa.create_engine(url)
    try:
        command.upgrade(config, "0001_initial")
        metadata = reflect_legacy_tables(engine)
        user_id, order_id, warehouse_id = [uuid4() for _ in range(3)]
        with engine.begin() as connection:
            connection.execute(
                metadata.tables["users"].insert(),
                {
                    "id": user_id,
                    "email": "legacy@example.com",
                    "hashed_password": "hash",
                    "full_name": "Legacy",
                    "role": "CUSTOMER",
                    "is_active": True,
                },
            )
            connection.execute(
                metadata.tables["warehouses"].insert(),
                {
                    "id": warehouse_id,
                    "code": "LEGACY",
                    "name": "Legacy",
                    "region": "test",
                },
            )
            connection.execute(
                metadata.tables["orders"].insert(),
                {
                    "id": order_id,
                    "customer_id": user_id,
                    "idempotency_key": "legacy",
                    "status": "PAID",
                    "total_amount": 10,
                    "currency": "USD",
                },
            )
            connection.execute(
                metadata.tables["order_items"].insert(),
                {
                    "id": uuid4(),
                    "order_id": order_id,
                    "sku": "SKU",
                    "product_name": "Legacy",
                    "quantity": 1,
                    "unit_price": 10,
                    "warehouse_id": warehouse_id,
                },
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            row = connection.execute(sa.text("SELECT request_hash, inventory_reserved FROM orders")).one()
            assert row.request_hash is None
            assert row.inventory_reserved == 1
        command.downgrade(config, "0001_initial")
        assert "request_hash" not in {c["name"] for c in sa.inspect(engine).get_columns("orders")}
        command.upgrade(config, "head")
        with engine.begin() as connection:
            metadata = reflect_legacy_tables(connection)
            second_user = uuid4()
            connection.execute(
                metadata.tables["users"].insert(),
                {
                    "id": second_user,
                    "email": "other@example.com",
                    "hashed_password": "hash",
                    "full_name": "Other",
                    "role": "CUSTOMER",
                    "is_active": True,
                },
            )
            connection.execute(
                metadata.tables["orders"].insert(),
                {
                    "id": uuid4(),
                    "customer_id": second_user,
                    "idempotency_key": "legacy",
                    "status": "CREATED",
                    "total_amount": 10,
                    "currency": "USD",
                },
            )
        command.downgrade(config, "0002_idempotency")
        with pytest.raises(RuntimeError, match="duplicate keys"):
            command.downgrade(config, "0001_initial")
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT COUNT(*) FROM orders")) == 2
    finally:
        engine.dispose()
        get_settings.cache_clear()
