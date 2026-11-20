"""Tenant ownership and catalog backfill, without changing native enums."""

import uuid
from decimal import Decimal

import sqlalchemy as sa

from alembic import op

revision = "0007_tenancy"
down_revision = "0006_replay"
branch_labels = None
depends_on = None
DEFAULT = uuid.UUID("00000000-0000-0000-0000-000000000001")
CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def ownership(table: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.add_column(sa.Column("tenant_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(f"fk_{table}_tenant", "tenants", ["tenant_id"], ["id"])


def unique_name(table: str, columns: list[str]) -> str:
    for constraint in sa.inspect(op.get_bind()).get_unique_constraints(table):
        if constraint["column_names"] == columns:
            return constraint["name"] or f"uq_{table}_{columns[0]}"
    raise RuntimeError(f"Missing legacy unique constraint on {table}: {columns}")


def upgrade() -> None:
    db = op.get_bind()
    if db.scalar(sa.text("SELECT COUNT(*) FROM payments WHERE length(provider) > 40")):
        raise RuntimeError("Payment provider names longer than 40 require reconciliation before upgrade")

    def timestamps() -> list[sa.Column]:
        return [
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        ]

    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("slug", sa.String(40), unique=True, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("default_currency", sa.String(3), nullable=False, server_default="INR"),
        sa.CheckConstraint("status IN ('active', 'suspended')", name="ck_tenant_status"),
        *timestamps(),
    )
    tenant_table = sa.table(
        "tenants",
        sa.column("id", sa.Uuid()),
        sa.column("name"),
        sa.column("slug"),
        sa.column("status"),
        sa.column("default_currency"),
    )
    db.execute(
        tenant_table.insert().values(
            id=DEFAULT, name="Default Store", slug="default", status="active", default_currency="INR"
        )
    )
    for table in ["users", "orders", "inventory", "warehouses", "outbox_events", "failed_jobs"]:
        ownership(table)
        statement = (
            sa.table(table, sa.column("tenant_id", sa.Uuid()))
            .update()
            .values(tenant_id=sa.bindparam("tenant", type_=sa.Uuid()))
        )
        db.execute(statement, {"tenant": DEFAULT})
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_check_constraint(
            "ck_users_tenant_principal",
            "(is_platform_admin AND tenant_id IS NULL) OR (NOT is_platform_admin AND tenant_id IS NOT NULL)",
        )
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("principal_scope", sa.String(50), nullable=True))
        for name, size in [("external_reference", 100), ("buyer_name", 120), ("buyer_email", 255), ("buyer_phone", 20)]:
            batch.add_column(sa.Column(name, sa.String(size), nullable=True))
        batch.alter_column("customer_id", nullable=True)
    for row in db.execute(sa.text("SELECT id, customer_id FROM orders")):
        statement = sa.text("UPDATE orders SET principal_scope = :scope WHERE id = :id").bindparams(
            sa.bindparam("id", type_=sa.Uuid())
        )
        db.execute(statement, {"id": uuid.UUID(str(row.id)), "scope": f"user:{uuid.UUID(str(row.customer_id))}"})
    with op.batch_alter_table("orders") as batch:
        batch.alter_column("tenant_id", nullable=False)
        batch.alter_column("principal_scope", nullable=False)
        batch.drop_constraint("uq_orders_customer_idempotency_key", type_="unique")
        batch.create_unique_constraint(
            "uq_orders_tenant_principal_key", ["tenant_id", "principal_scope", "idempotency_key"]
        )
        batch.create_index("ix_orders_tenant_created", ["tenant_id", "created_at"])
    for table in ["inventory", "warehouses"]:
        old_columns = ["sku", "warehouse_id"] if table == "inventory" else ["code"]
        old_name = unique_name(table, old_columns)
        with op.batch_alter_table(table, naming_convention=CONVENTION) as batch:
            batch.alter_column("tenant_id", nullable=False)
            batch.drop_constraint(old_name, type_="unique")
            batch.create_unique_constraint(f"uq_{table}_tenant_identity", ["tenant_id", *old_columns])
    with op.batch_alter_table("outbox_events") as batch:
        batch.create_index("ix_outbox_tenant_event", ["tenant_id", "event_type"])
    for table in ["payments", "shipments"]:
        with op.batch_alter_table(table) as batch:
            if table == "payments":
                batch.alter_column("provider", type_=sa.String(40), nullable=True)
            else:
                batch.add_column(sa.Column("provider", sa.String(40), nullable=True))
            batch.add_column(sa.Column("provider_reference", sa.String(120), nullable=True))
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
        sa.CheckConstraint("price >= 0", name="ck_product_price_non_negative"),
        *timestamps(),
    )
    product_table = sa.table(
        "products",
        sa.column("id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("sku"),
        sa.column("name"),
        sa.column("price", sa.Numeric(12, 2)),
        sa.column("currency"),
        sa.column("is_active"),
    )
    for row in db.execute(sa.text("SELECT sku, MIN(product_name) AS product_name FROM inventory GROUP BY sku")):
        price = db.scalar(
            sa.text("SELECT unit_price FROM order_items WHERE sku=:sku ORDER BY created_at DESC, id DESC LIMIT 1"),
            {"sku": row.sku},
        )
        db.execute(
            product_table.insert().values(
                id=uuid.uuid4(),
                tenant_id=DEFAULT,
                sku=row.sku,
                name=row.product_name,
                price=Decimal(str(price)) if price is not None else Decimal("49.99"),
                currency="INR",
                is_active=True,
            )
        )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("key_prefix", sa.String(12), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id")),
        *timestamps(),
    )
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"], unique=True)


def downgrade() -> None:
    db = op.get_bind()
    if db.scalar(sa.text("SELECT COUNT(*) FROM tenants")) > 1:
        raise RuntimeError("Cannot downgrade multiple tenants; reconcile tenant data first")
    if db.scalar(sa.text("SELECT COUNT(*) FROM orders WHERE customer_id IS NULL")):
        raise RuntimeError("Cannot downgrade tenant API orders with NULL customer_id")
    if db.scalar(sa.text("SELECT COUNT(*) FROM users WHERE is_platform_admin")):
        raise RuntimeError("Cannot downgrade platform admins without losing their principal identity")
    if db.scalar(sa.text("SELECT COUNT(*) FROM api_keys")):
        raise RuntimeError("Cannot downgrade tenant API key records without losing metadata")
    duplicates = db.execute(
        sa.text("SELECT customer_id,idempotency_key FROM orders GROUP BY customer_id,idempotency_key HAVING COUNT(*)>1")
    ).first()
    if duplicates:
        raise RuntimeError("Cannot restore customer idempotency uniqueness")
    # Refuse before the first DDL operation: SQLite DDL is not fully transactional.
    if db.scalar(sa.text("SELECT COUNT(*) FROM payments WHERE provider IS NULL")):
        raise RuntimeError("Cannot restore non-null payment provider")
    if db.scalar(
        sa.text(
            "SELECT COUNT(*) FROM orders WHERE external_reference IS NOT NULL OR buyer_name IS NOT NULL "
            "OR buyer_email IS NOT NULL OR buyer_phone IS NOT NULL"
        )
    ):
        raise RuntimeError("Cannot downgrade new order metadata without losing data")
    for table in ["payments", "shipments"]:
        retained = sa.table(table, sa.column("provider_reference"))
        if db.scalar(
            sa.select(sa.func.count()).select_from(retained).where(retained.c.provider_reference.is_not(None))
        ):
            raise RuntimeError("Cannot downgrade new provider references without losing data")
    currency = db.scalar(sa.text("SELECT default_currency FROM tenants"))
    for product in db.execute(sa.text("SELECT sku,name,description,price,currency,is_active FROM products")):
        name = db.scalar(sa.text("SELECT MIN(product_name) FROM inventory WHERE sku=:sku"), {"sku": product.sku})
        price = db.scalar(
            sa.text("SELECT unit_price FROM order_items WHERE sku=:sku ORDER BY created_at DESC,id DESC LIMIT 1"),
            {"sku": product.sku},
        )
        expected = Decimal(str(price)) if price is not None else Decimal("49.99")
        if (
            name != product.name
            or product.description is not None
            or not product.is_active
            or Decimal(str(product.price)) != expected
            or product.currency != currency
        ):
            raise RuntimeError("Cannot downgrade tenant catalog data that cannot be reconstructed")
    op.drop_table("api_keys")
    op.drop_table("products")
    for table in ["payments", "shipments"]:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("provider_reference")
            if table == "payments":
                if db.scalar(sa.text("SELECT COUNT(*) FROM payments WHERE provider IS NULL")):
                    raise RuntimeError("Cannot restore non-null payment provider")
                batch.alter_column("provider", type_=sa.String(60), nullable=False)
            else:
                batch.drop_column("provider")
    with op.batch_alter_table("orders") as batch:
        batch.drop_index("ix_orders_tenant_created")
        batch.drop_constraint("uq_orders_tenant_principal_key", type_="unique")
        batch.create_unique_constraint("uq_orders_customer_idempotency_key", ["customer_id", "idempotency_key"])
        batch.alter_column("customer_id", nullable=False)
        for name in ["principal_scope", "external_reference", "buyer_name", "buyer_email", "buyer_phone"]:
            batch.drop_column(name)
    for table in ["inventory", "warehouses"]:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"uq_{table}_tenant_identity", type_="unique")
            batch.create_unique_constraint(
                "uq_inventory_sku_warehouse" if table == "inventory" else "uq_warehouses_code",
                ["sku", "warehouse_id"] if table == "inventory" else ["code"],
            )
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_tenant_principal", type_="check")
        batch.drop_column("is_platform_admin")
    with op.batch_alter_table("outbox_events") as batch:
        batch.drop_index("ix_outbox_tenant_event")
    for table in ["users", "orders", "inventory", "warehouses", "outbox_events", "failed_jobs"]:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_tenant", type_="foreignkey")
            batch.drop_column("tenant_id")
    op.drop_table("tenants")
