"""Recoverable refund claims protect provider calls without holding row locks."""

import sqlalchemy as sa

from alembic import op

revision = "0014_refund_claims"
down_revision = "0013_refunds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("refunds") as batch:
        batch.add_column(sa.Column("claim_token", sa.Uuid()))
        batch.add_column(sa.Column("lease_until", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column("failed_job_id", sa.Uuid(), sa.ForeignKey("failed_jobs.id", name="fk_refund_failed_job"))
        )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM refunds WHERE claim_token IS NOT NULL "
            "OR lease_until IS NOT NULL OR failed_job_id IS NOT NULL"
        )
    ):
        raise RuntimeError("Cannot downgrade: operational refund claim/failure metadata requires archival")
    with op.batch_alter_table("refunds") as batch:
        batch.drop_constraint("fk_refund_failed_job", type_="foreignkey")
        batch.drop_column("failed_job_id")
        batch.drop_column("lease_until")
        batch.drop_column("claim_token")
