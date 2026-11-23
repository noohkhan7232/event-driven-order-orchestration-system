"""Durable notification intents; portable statuses and opt-out setting."""

import sqlalchemy as sa

from alembic import op

revision = "0020_notifications"
down_revision = "0019_outbound_webhooks"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tenants", sa.Column("notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("template", sa.String(40), nullable=False),
        sa.Column("recipient_hint", sa.String(255)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(80)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("claim_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("order_id", "channel", "template", name="uq_order_channel_template"),
        sa.CheckConstraint("status IN ('pending','sent','failed','skipped')", name="ck_notification_status"),
    )
    op.create_index("ix_notification_due", "notifications", ["status", "next_attempt_at"])


def downgrade():
    conn = op.get_bind()
    if conn.scalar(sa.text("SELECT COUNT(*) FROM notifications")) or conn.scalar(
        sa.text("SELECT COUNT(*) FROM tenants WHERE notifications_enabled=false")
    ):
        raise RuntimeError("Cannot downgrade: operational notifications/settings require archival")
    op.drop_table("notifications")
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("notifications_enabled")
