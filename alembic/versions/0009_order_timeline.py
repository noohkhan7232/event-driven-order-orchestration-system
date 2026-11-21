"""Persist order timeline; legacy rows are synthetic observations, not invented history."""

import sqlalchemy as sa

from alembic import op

revision = "0009_timeline"
down_revision = "0008_key_prefix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_timeline",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("from_status", sa.String(40), nullable=True),
        sa.Column("to_status", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_order_timeline_tenant_id", "order_timeline", ["tenant_id", "id"])
    op.create_index("ix_order_timeline_order_id", "order_timeline", ["order_id", "id"])
    op.execute(
        sa.text(
            "INSERT INTO order_timeline (tenant_id,order_id,from_status,to_status,reason) "
            "SELECT tenant_id,id,NULL,lower(CAST(status AS VARCHAR)),'MIGRATION_BACKFILL' FROM orders"
        )
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM order_timeline WHERE from_status IS NOT NULL "
            "OR reason IS NULL OR reason != 'MIGRATION_BACKFILL'"
        )
    ):
        raise RuntimeError("Cannot downgrade: operational order timeline history requires explicit archival first")
    op.drop_table("order_timeline")
