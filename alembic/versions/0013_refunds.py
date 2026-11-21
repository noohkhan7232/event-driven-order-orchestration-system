"""Durable refund requests required by captured-payment compensation."""

import sqlalchemy as sa

from alembic import op

revision = "0013_refunds"
down_revision = "0012_inbound"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refunds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("payment_id", sa.Uuid(), sa.ForeignKey("payments.id"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("provider_refund_id", sa.String(120)),
        sa.Column("idempotency_key", sa.String(120), unique=True, nullable=False),
        sa.Column("last_error", sa.String(240)),
        sa.Column("correlation_id", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('requested','processing','succeeded','failed')", name="ck_refund_status"),
    )
    op.create_index("ix_refunds_provider_refund_id", "refunds", ["provider_refund_id"])


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM refunds")):
        raise RuntimeError("Cannot downgrade: durable refunds require explicit archival")
    op.drop_table("refunds")
