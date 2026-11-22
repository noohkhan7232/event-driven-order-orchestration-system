"""Scope inbound deduplication to the seller without deleting receipt history."""

import sqlalchemy as sa

from alembic import op

revision = "0015_tenant_receipts"
down_revision = "0014_refund_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("inbound_webhook_events") as batch:
        batch.drop_constraint("uq_inbound_provider_event", type_="unique")
        batch.create_unique_constraint("uq_inbound_provider_event", ["provider", "tenant_id", "provider_event_id"])


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM (SELECT provider, provider_event_id FROM inbound_webhook_events "
            "GROUP BY provider, provider_event_id HAVING COUNT(*) > 1) AS conflicts"
        )
    ):
        raise RuntimeError("Cannot downgrade: cross-tenant event ID conflicts require explicit archival")
    with op.batch_alter_table("inbound_webhook_events") as batch:
        batch.drop_constraint("uq_inbound_provider_event", type_="unique")
        batch.create_unique_constraint("uq_inbound_provider_event", ["provider", "provider_event_id"])
