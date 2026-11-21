"""Encrypted tenant-owned payment settings; native enums are unchanged."""

import sqlalchemy as sa

from alembic import op

revision = "0010_payment_config"
down_revision = "0009_timeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_payment_configs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("key_id", sa.String(120), nullable=False),
        sa.Column("key_secret_enc", sa.Text(), nullable=False),
        sa.Column("webhook_secret_enc", sa.Text(), nullable=False),
        sa.Column("webhook_public_id", sa.String(80), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_tenant_payment_provider"),
        sa.CheckConstraint("provider = 'razorpay'", name="ck_payment_config_provider"),
        sa.CheckConstraint("mode IN ('test', 'live')", name="ck_payment_config_mode"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM tenant_payment_configs")):
        raise RuntimeError("Cannot downgrade: tenant payment credentials require explicit archival first")
    op.drop_table("tenant_payment_configs")
