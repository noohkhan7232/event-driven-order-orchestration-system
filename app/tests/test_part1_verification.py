import os
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session
from test_payment_config import identity
from test_payment_config import payment_settings as payment_settings

from alembic import command
from app.core.config import get_settings
from app.models.entities import (
    DEFAULT_TENANT_ID,
    ApiKey,
    Inventory,
    OrderItem,
    PaymentReconciliation,
    Product,
    TenantPaymentConfig,
    User,
    Warehouse,
)
from app.models.enums import UserRole


def legacy_roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = sa.create_engine(url, hide_parameters=True)
    config = Config("alembic.ini")
    order_id, payment_id = uuid4(), uuid4()
    try:
        command.upgrade(config, "0007_tenancy")
        orders = sa.Table(
            "orders",
            sa.MetaData(),
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("tenant_id", sa.Uuid()),
            sa.Column("customer_id", sa.Uuid()),
            autoload_with=engine,
        )
        payments = sa.Table(
            "payments",
            sa.MetaData(),
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("order_id", sa.Uuid()),
            autoload_with=engine,
        )
        with Session(engine) as db:
            user = User(
                tenant_id=DEFAULT_TENANT_ID,
                email="legacy-proof@example.com",
                full_name="Legacy",
                hashed_password="unused",
                role=UserRole.CUSTOMER,
            )
            warehouse = Warehouse(tenant_id=DEFAULT_TENANT_ID, code="LEGACY", name="Legacy", region="test")
            db.add_all(
                [
                    user,
                    warehouse,
                    Product(
                        tenant_id=DEFAULT_TENANT_ID, sku="LEGACY", name="Legacy", price=Decimal("19.99"), currency="INR"
                    ),
                ]
            )
            db.flush()
            inventory = Inventory(
                tenant_id=DEFAULT_TENANT_ID,
                sku="LEGACY",
                product_name="Legacy",
                warehouse_id=warehouse.id,
                available_quantity=7,
                reserved_quantity=3,
            )
            key = ApiKey(
                tenant_id=DEFAULT_TENANT_ID,
                name="Legacy",
                key_prefix="oos_live_abc",
                key_hash="0" * 64,
                scopes=["orders:read"],
            )
            db.add_all([inventory, key])
            db.execute(
                orders.insert().values(
                    id=order_id,
                    tenant_id=DEFAULT_TENANT_ID,
                    customer_id=user.id,
                    principal_scope=f"user:{user.id}",
                    idempotency_key="legacy-proof",
                    status="PAID",
                    total_amount=Decimal("59.97"),
                    currency="INR",
                    inventory_reserved=True,
                )
            )
            db.add(
                OrderItem(
                    order_id=order_id,
                    sku="LEGACY",
                    product_name="Legacy",
                    quantity=3,
                    unit_price=Decimal("19.99"),
                    warehouse_id=warehouse.id,
                )
            )
            db.execute(
                payments.insert().values(
                    id=payment_id,
                    order_id=order_id,
                    provider="simulation_gateway",
                    provider_reference="pay_legacy",
                    provider_transaction_id="pay_legacy",
                    status="AUTHORIZED",
                    amount=Decimal("59.97"),
                )
            )
            db.commit()
            inventory_id, key_id = inventory.id, key.id

        def snapshot():
            with engine.connect() as connection:
                data = (
                    connection.execute(
                        sa.select(
                            orders.c.id,
                            orders.c.total_amount,
                            orders.c.currency,
                            orders.c.status,
                            orders.c.inventory_reserved,
                        ).where(orders.c.id == order_id)
                    ).one(),
                    connection.execute(
                        sa.select(
                            payments.c.id, payments.c.amount, payments.c.status, payments.c.provider_transaction_id
                        ).where(payments.c.id == payment_id)
                    ).one(),
                    connection.execute(
                        sa.select(Inventory.available_quantity, Inventory.reserved_quantity).where(
                            Inventory.id == inventory_id
                        )
                    ).one(),
                    connection.execute(
                        sa.select(OrderItem.quantity, OrderItem.unit_price).where(OrderItem.order_id == order_id)
                    ).one(),
                    connection.execute(sa.select(ApiKey.key_prefix, ApiKey.key_hash).where(ApiKey.id == key_id)).one(),
                )
                labels = (
                    connection.execute(
                        sa.text(
                            "SELECT t.typname,e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid "
                            "JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname=current_schema() "
                            "ORDER BY t.typname,e.enumsortorder"
                        )
                    ).all()
                    if engine.dialect.name == "postgresql"
                    else []
                )
                return data, labels

        baseline = snapshot()
        command.upgrade(config, "head")
        assert snapshot() == baseline
        with engine.connect() as connection:
            assert (
                connection.scalar(sa.text("SELECT COUNT(*) FROM order_timeline WHERE reason='MIGRATION_BACKFILL'")) == 1
            )
        command.downgrade(config, "0007_tenancy")
        assert snapshot() == baseline
        command.upgrade(config, "head")
        assert snapshot() == baseline
        with Session(engine) as db:
            db.add(
                PaymentReconciliation(
                    payment_id=payment_id, tenant_id=DEFAULT_TENANT_ID, order_id=order_id, claim_token=uuid4()
                )
            )
            db.commit()
        with pytest.raises(RuntimeError, match="polling checkpoints require explicit archival"):
            command.downgrade(config, "0007_tenancy")
        assert snapshot() == baseline
        print(
            "0007->HEAD->0007->HEAD: LEGACY ORDER/PAYMENT/ITEM/INVENTORY/PREFIX/HASH AND NATIVE ENUM LABELS PRESERVED"
        )
        print("OPERATIONAL CHECKPOINT DOWNGRADE REFUSAL: BEFORE DATA LOSS; FINANCIAL SNAPSHOT UNCHANGED")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_full_part1_legacy_migration(tmp_path, monkeypatch):
    legacy_roundtrip(f"sqlite:///{tmp_path / 'part1.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_full_part1_legacy_migration(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    schema = "part1_" + uuid4().hex
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(sa.text(f"CREATE SCHEMA {schema}"))
    try:
        scoped = sa.engine.make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
        legacy_roundtrip(scoped.render_as_string(hide_password=False), monkeypatch)
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


def test_all_five_part1_routes_keep_credential_material_private(client, db, workflow, payment_settings):
    from test_inbound_payments import signed

    _, stock = workflow
    _, auth = identity(db)
    path = "/api/v1/settings/payments/razorpay"
    configured = client.put(
        path,
        headers=auth,
        json={
            "key_id": "public1234",
            "key_secret": "guarded-api-value",
            "webhook_secret": "synthetic-hook",
            "mode": "test",
        },
    )
    assert configured.status_code == 200
    row = db.query(TenantPaymentConfig).one()
    secret_values = [
        "guarded-api-value",
        "synthetic-hook",
        row.key_secret_enc,
        row.webhook_secret_enc,
        payment_settings.credentials_encryption_key.get_secret_value(),
        "private-buyer@example.com",
    ]
    raw, headers = signed()
    responses = [
        configured,
        client.get(path, headers=auth),
        client.post(
            f"/api/v1/inventory/{stock.id}/adjustments", headers=auth, json={"delta": 1, "reason": "Verification"}
        ),
        client.post(
            "/api/v1/webhooks/payments/razorpay/" + configured.json()["webhook_public_id"], content=raw, headers=headers
        ),
        client.delete(path, headers=auth),
    ]
    assert [response.status_code for response in responses] == [200, 200, 200, 200, 204]
    for response in responses:
        exposed = response.text + repr(dict(response.headers))
        assert all(secret not in exposed for secret in secret_values)
