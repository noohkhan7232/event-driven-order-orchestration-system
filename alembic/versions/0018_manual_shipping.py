"""Manual shipping metadata; retain legacy widths and native enums."""

import sqlalchemy as sa

from alembic import op

revision = "0018_manual_shipping"
down_revision = "0017_payment_polling"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("shipping_provider", sa.String(16), nullable=False, server_default="manual"))
    # Existing tenants keep the previously available provider; fresh ORM tenants
    # snapshot SHIPPING_PROVIDER. Raw inserts get the production-safe manual default.
    op.execute("UPDATE tenants SET shipping_provider='mock'")
    op.add_column("shipments", sa.Column("tracking_url", sa.String(300)))
    op.add_column("shipments", sa.Column("estimated_delivery", sa.Date()))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.scalar(sa.text("SELECT COUNT(*) FROM tenants WHERE shipping_provider <> 'mock'")) or conn.scalar(
        sa.text(
            "SELECT COUNT(*) FROM shipments WHERE tracking_url IS NOT NULL "
            "OR estimated_delivery IS NOT NULL OR provider='manual'"
        )
    ):
        raise RuntimeError("Cannot downgrade: operational manual shipping configuration/metadata requires archival")
    with op.batch_alter_table("shipments") as batch:
        batch.drop_column("estimated_delivery")
        batch.drop_column("tracking_url")
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("shipping_provider")
