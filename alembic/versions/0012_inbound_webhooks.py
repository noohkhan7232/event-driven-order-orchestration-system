"""Durable verified inbound payment receipts, without changing native enums."""

import sqlalchemy as sa

from alembic import op

revision = "0012_inbound"
down_revision = "0011_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbound_webhook_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_event_id", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_inbound_provider_event"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM inbound_webhook_events")):
        raise RuntimeError("Cannot downgrade: inbound payment receipts require explicit archival")
    op.drop_table("inbound_webhook_events")
