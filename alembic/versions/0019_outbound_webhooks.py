"""Tenant webhook subscriptions and durable leased deliveries; portable strings."""

import sqlalchemy as sa

from alembic import op

revision = "0019_outbound_webhooks"
down_revision = "0018_manual_shipping"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("description", sa.String(200)),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("disabled_reason", sa.String(80)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index("ix_webhook_endpoints_tenant_id", "webhook_endpoints", ["tenant_id"])
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("endpoint_id", sa.Uuid(), sa.ForeignKey("webhook_endpoints.id"), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_status_code", sa.Integer()),
        sa.Column("last_error", sa.String(240)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("replay_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("claim_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("endpoint_id", "event_id", name="uq_webhook_endpoint_event"),
        sa.CheckConstraint("status IN ('pending','delivered','failed','dead')", name="ck_webhook_delivery_status"),
    )
    op.create_index("ix_webhook_delivery_due", "webhook_deliveries", ["status", "next_attempt_at"])
    op.create_index("ix_webhook_delivery_owner", "webhook_deliveries", ["tenant_id", "endpoint_id", "created_at"])


def downgrade() -> None:
    conn = op.get_bind()
    if conn.scalar(sa.text("SELECT COUNT(*) FROM webhook_endpoints")) or conn.scalar(
        sa.text("SELECT COUNT(*) FROM webhook_deliveries")
    ):
        raise RuntimeError("Cannot downgrade: operational webhook subscriptions/deliveries require explicit archival")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhook_endpoints")
