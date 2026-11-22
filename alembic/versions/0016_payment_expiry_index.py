"""Index the global bounded expiry scan without changing financial rows/enums."""

from alembic import op

revision = "0016_expiry_index"
down_revision = "0015_tenant_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_orders_payment_expiry", "orders", ["status", "payment_expires_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_orders_payment_expiry", table_name="orders")
