"""Revocable public tracking versions; no native enum changes."""

import sqlalchemy as sa

from alembic import op

revision = "0021_public_tracking"
down_revision = "0020_notifications"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("tracking_version", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM orders WHERE tracking_version <> 1")):
        raise RuntimeError("Cannot downgrade: rotated tracking versions require archival")
    with op.batch_alter_table("orders") as batch:
        batch.drop_column("tracking_version")
