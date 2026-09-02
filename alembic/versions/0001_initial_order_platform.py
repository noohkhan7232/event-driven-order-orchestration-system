"""initial order platform schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def uuid_type():
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column("role", sa.Enum("CUSTOMER", "ADMIN", "VENDOR", name="userrole"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", uuid_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "warehouses",
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("region", sa.String(80), nullable=False),
        sa.Column("id", uuid_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    op.create_table(
        "orders",
        sa.Column("customer_id", uuid_type(), nullable=False),
        sa.Column("status", sa.Enum("CREATED", "VALIDATED", "INVENTORY_RESERVED", "PAYMENT_PENDING", "PAID", "FULFILLMENT_REQUESTED", "SHIPPED", "DELIVERED", "CANCELLED", "FAILED", name="orderstatus"), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("id", uuid_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
    )
    op.create_index("ix_orders_customer_status", "orders", ["customer_id", "status"])
    op.create_index("ix_orders_created_status", "orders", ["created_at", "status"])

    op.create_table(
        "inventory",
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("product_name", sa.String(180), nullable=False),
        sa.Column("warehouse_id", uuid_type(), nullable=False),
        sa.Column("available_quantity", sa.Integer(), nullable=False),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False),
        sa.Column("low_stock_threshold", sa.Integer(), nullable=False),
        sa.Column("id", uuid_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("available_quantity >= 0", name="ck_inventory_available_non_negative"),
        sa.CheckConstraint("reserved_quantity >= 0", name="ck_inventory_reserved_non_negative"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sku", "warehouse_id", name="uq_inventory_sku_warehouse"),
    )
    op.create_index("ix_inventory_sku_available", "inventory", ["sku", "available_quantity"])

    op.create_table(
        "order_items",
        sa.Column("order_id", uuid_type(), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("product_name", sa.String(180), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("warehouse_id", uuid_type(), nullable=True),
        sa.Column("id", uuid_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_order_items_order_sku", "order_items", ["order_id", "sku"])

    op.create_table(
        "payments",
        sa.Column("order_id", uuid_type(), nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("provider_transaction_id", sa.String(120), nullable=True),
        sa.Column("status", sa.Enum("INITIATED", "AUTHORIZED", "CAPTURED", "FAILED", "REFUNDED", name="paymentstatus"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("failure_code", sa.String(80), nullable=True),
        sa.Column("id", uuid_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_transaction_id"),
    )
    op.create_index("ix_payments_order_status", "payments", ["order_id", "status"])

    op.create_table(
        "shipments",
        sa.Column("order_id", uuid_type(), nullable=False),
        sa.Column("carrier", sa.String(80), nullable=False),
        sa.Column("tracking_number", sa.String(120), nullable=True),
        sa.Column("status", sa.Enum("PENDING", "CREATED", "IN_TRANSIT", "DELAYED", "DELIVERED", "CANCELLED", name="shipmentstatus"), nullable=False),
        sa.Column("estimated_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", uuid_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tracking_number"),
    )
    op.create_index("ix_shipments_order_status", "shipments", ["order_id", "status"])

    op.create_table(
        "audit_logs",
        sa.Column("actor_id", uuid_type(), nullable=True),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", uuid_type(), nullable=False),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("id", uuid_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_entity", "audit_logs", ["entity_type", "entity_id"])

    op.create_table(
        "failed_jobs",
        sa.Column("task_name", sa.String(160), nullable=False),
        sa.Column("queue_name", sa.String(80), nullable=False),
        sa.Column("status", sa.Enum("QUEUED", "RUNNING", "RETRYING", "FAILED", "COMPLETED", name="jobstatus"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("id", uuid_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_failed_jobs_task_status", "failed_jobs", ["task_name", "status"])

    op.create_table(
        "shipment_events",
        sa.Column("shipment_id", uuid_type(), nullable=False),
        sa.Column("status", sa.Enum("PENDING", "CREATED", "IN_TRANSIT", "DELAYED", "DELIVERED", "CANCELLED", name="shipmentstatus"), nullable=False),
        sa.Column("location", sa.String(160), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", uuid_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["shipment_id"], ["shipments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("shipment_events")
    op.drop_index("ix_failed_jobs_task_status", table_name="failed_jobs")
    op.drop_table("failed_jobs")
    op.drop_index("ix_audit_entity", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_shipments_order_status", table_name="shipments")
    op.drop_table("shipments")
    op.drop_index("ix_payments_order_status", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_order_items_order_sku", table_name="order_items")
    op.drop_table("order_items")
    op.drop_index("ix_inventory_sku_available", table_name="inventory")
    op.drop_table("inventory")
    op.drop_index("ix_orders_created_status", table_name="orders")
    op.drop_index("ix_orders_customer_status", table_name="orders")
    op.drop_table("orders")
    op.drop_table("warehouses")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
