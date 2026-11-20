from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from app.core.config import get_settings


def legacy_roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    engine = sa.create_engine(url)
    try:
        command.upgrade(config, "0006_replay")
        from test_migrations_hardening import reflect_legacy_tables

        metadata = reflect_legacy_tables(engine)
        user, warehouse, order = uuid4(), uuid4(), uuid4()
        with engine.begin() as conn:
            conn.execute(
                metadata.tables["users"].insert(),
                dict(
                    id=user,
                    email="legacy-tenant@example.com",
                    hashed_password="hash",
                    full_name="Legacy",
                    role="CUSTOMER",
                    is_active=True,
                ),
            )
            conn.execute(
                metadata.tables["warehouses"].insert(), dict(id=warehouse, code="LEGACY", name="Legacy", region="test")
            )
            conn.execute(
                metadata.tables["inventory"].insert(),
                dict(
                    id=uuid4(),
                    sku="LEGACY-SKU",
                    product_name="Legacy Product",
                    warehouse_id=warehouse,
                    available_quantity=10,
                    reserved_quantity=0,
                    low_stock_threshold=2,
                ),
            )
            conn.execute(
                metadata.tables["orders"].insert(),
                dict(
                    id=order,
                    customer_id=user,
                    status="CREATED",
                    idempotency_key="legacy",
                    total_amount=39.98,
                    currency="USD",
                    inventory_reserved=False,
                ),
            )
            conn.execute(
                metadata.tables["order_items"].insert(),
                dict(
                    id=uuid4(),
                    order_id=order,
                    sku="LEGACY-SKU",
                    product_name="Legacy Product",
                    quantity=2,
                    unit_price=19.99,
                ),
            )
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert "tenants" in sa.inspect(conn).get_table_names()
            tenant = conn.execute(sa.text("SELECT id, slug, default_currency FROM tenants")).one()
            assert tenant.slug == "default" and tenant.default_currency == "INR"
            row = conn.execute(sa.text("SELECT principal_scope, tenant_id FROM orders")).one()
            assert row.principal_scope == f"user:{user}"
            assert UUID(str(row.tenant_id)) == UUID(str(tenant.id))
            product = conn.execute(sa.text("SELECT sku, name, price, currency FROM products")).one()
            assert product.sku == "LEGACY-SKU" and product.name == "Legacy Product"
            assert float(product.price) == 19.99 and product.currency == "INR"
        command.downgrade(config, "0006_replay")
        with engine.connect() as conn:
            assert conn.scalar(sa.text("SELECT COUNT(*) FROM orders")) == 1
            assert "tenant_id" not in {c["name"] for c in sa.inspect(conn).get_columns("users")}
        command.upgrade(config, "head")
        with engine.begin() as conn:
            tenants = sa.Table("tenants", sa.MetaData(), autoload_with=conn)
            tenants.c.id.type = sa.Uuid()
            conn.execute(
                tenants.insert(),
                dict(id=uuid4(), name="Another", slug="another", status="active", default_currency="USD"),
            )
        with pytest.raises(RuntimeError, match="tenant"):
            command.downgrade(config, "0006_replay")
        print("LEGACY BACKFILL/ROUNDTRIP PASS; MULTI-TENANT DOWNGRADE REFUSAL PASS")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_tenancy_legacy_roundtrip(tmp_path, monkeypatch):
    legacy_roundtrip(f"sqlite:///{tmp_path / 'tenancy.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_tenancy_legacy_roundtrip(monkeypatch):
    import os

    from sqlalchemy.engine import make_url

    test_url = make_url(os.environ["TEST_DATABASE_URL"])
    # This test uses its own disposable schema, never the application/test tables.
    schema = "migration_" + uuid4().hex
    setup = sa.create_engine(test_url)
    with setup.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    try:
        scoped = test_url.update_query_dict({"options": f"-csearch_path={schema}"})
        legacy_roundtrip(scoped.render_as_string(hide_password=False), monkeypatch)
    finally:
        with setup.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        setup.dispose()
