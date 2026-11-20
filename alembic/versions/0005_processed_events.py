"""Consumer deduplication markers are committed with handler effects."""

import sqlalchemy as sa

from alembic import op

revision = "0005_consumers"
down_revision = "0004_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processed_events",
        sa.Column("consumer", sa.String(100), primary_key=True),
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("processed_events")
