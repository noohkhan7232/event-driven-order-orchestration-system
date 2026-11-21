"""Portable payment session state, preserving all native enum labels."""

import sqlalchemy as sa

from alembic import op

revision = "0011_sessions"
down_revision = "0010_payment_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("payment_method", sa.String(12), server_default="prepaid", nullable=False))
    op.add_column("orders", sa.Column("payment_expires_at", sa.DateTime(timezone=True)))
    op.add_column("payments", sa.Column("idempotency_key", sa.String(100)))
    op.add_column("payments", sa.Column("provider_status", sa.String(40)))
    op.add_column("payments", sa.Column("checkout_payload", sa.JSON()))
    op.create_index("uq_payments_session_key", "payments", ["idempotency_key"], unique=True)


def downgrade() -> None:
    db = op.get_bind()
    if db.scalar(
        sa.text("SELECT COUNT(*) FROM orders WHERE payment_method != 'prepaid' OR payment_expires_at IS NOT NULL")
    ) or db.scalar(
        sa.text(
            "SELECT COUNT(*) FROM payments WHERE idempotency_key IS NOT NULL "
            "OR provider_status IS NOT NULL OR checkout_payload IS NOT NULL"
        )
    ):
        raise RuntimeError("Cannot downgrade while operational payment session records exist")
    op.drop_index("uq_payments_session_key", table_name="payments")
    for column in ["idempotency_key", "provider_status", "checkout_payload"]:
        op.drop_column("payments", column)
    op.drop_column("orders", "payment_expires_at")
    op.drop_column("orders", "payment_method")
