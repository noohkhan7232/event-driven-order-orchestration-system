"""Track outstanding inventory reservations for safe sequential compensation."""

import sqlalchemy as sa

from alembic import op

revision = "0003_reservation"
down_revision = "0002_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("inventory_reserved", sa.Boolean(), nullable=False, server_default=sa.false()))
    # The old worker keeps reservations through fulfillment. Infer those legacy
    # reservations from workflow status plus a warehouse assignment.
    op.execute(
        sa.text("""
        UPDATE orders SET inventory_reserved = true
        WHERE status IN ('INVENTORY_RESERVED', 'PAYMENT_PENDING', 'PAID',
                         'FULFILLMENT_REQUESTED', 'SHIPPED', 'DELIVERED')
          AND EXISTS (SELECT 1 FROM order_items
                      WHERE order_items.order_id = orders.id AND warehouse_id IS NOT NULL)
    """)
    )


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("inventory_reserved")
