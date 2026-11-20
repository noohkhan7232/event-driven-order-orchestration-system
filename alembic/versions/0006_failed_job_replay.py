"""Store full failed-event identifiers and portable replay status.

Postgres jobstatus is converted to VARCHAR, avoiding an unsafe native enum add.
The old enum type is retained for a data-preserving downgrade.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_replay"
down_revision = "0005_consumers"
branch_labels = None
depends_on = None
OLD = ("QUEUED", "RUNNING", "RETRYING", "FAILED", "COMPLETED")


def upgrade() -> None:
    with op.batch_alter_table("failed_jobs") as batch:
        batch.add_column(sa.Column("event_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("aggregate_id", sa.Uuid(), nullable=True))
        batch.alter_column(
            "status",
            existing_type=sa.Enum(*OLD, name="jobstatus"),
            type_=sa.String(20),
            existing_nullable=False,
            postgresql_using="status::text",
        )


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM failed_jobs WHERE status = 'REPLAYED'")):
        raise RuntimeError("Cannot downgrade replayed failed jobs; reconcile their status first")
    original = (
        postgresql.ENUM(*OLD, name="jobstatus", create_type=False)
        if op.get_bind().dialect.name == "postgresql"
        else sa.Enum(*OLD, name="jobstatus")
    )
    with op.batch_alter_table("failed_jobs") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(20),
            type_=original,
            existing_nullable=False,
            postgresql_using="status::jobstatus",
        )
        batch.drop_column("aggregate_id")
        batch.drop_column("event_id")
