"""Durable payment polling claims; no changes to native lifecycle enums."""

import sqlalchemy as sa

from alembic import op

revision = "0017_payment_polling"
down_revision = "0016_expiry_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_reconciliation",
        sa.Column("payment_id", sa.Uuid(), sa.ForeignKey("payments.id"), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("claim_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM payment_reconciliation")):
        raise RuntimeError("Cannot downgrade: operational payment polling checkpoints require explicit archival")
    op.drop_table("payment_reconciliation")
