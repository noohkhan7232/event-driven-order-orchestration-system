"""Widen key lookup prefixes while preserving legacy hashes."""

import sqlalchemy as sa

from alembic import op

revision = "0008_key_prefix"
down_revision = "0007_tenancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch:
        batch.alter_column("key_prefix", existing_type=sa.String(12), type_=sa.String(24), existing_nullable=False)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM api_keys WHERE length(key_prefix) > 12")):
        raise RuntimeError(
            "Cannot downgrade API key prefixes: newer keys require 17-character prefixes; "
            "retain the schema or explicitly retire these rows"
        )
    with op.batch_alter_table("api_keys") as batch:
        batch.alter_column("key_prefix", existing_type=sa.String(24), type_=sa.String(12), existing_nullable=False)
