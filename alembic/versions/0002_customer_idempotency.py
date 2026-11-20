"""Scope idempotency by customer and store validated request fingerprints."""

import sqlalchemy as sa

from alembic import op

revision = "0002_idempotency"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("uq_orders_idempotency_key", type_="unique")
        batch.create_unique_constraint("uq_orders_customer_idempotency_key", ["customer_id", "idempotency_key"])
        # Old requests were not retained. Runtime reconstructs hashes from currency/items
        # for legacy rows; new orders always store a fingerprint.
        batch.add_column(sa.Column("request_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    # Refuse rather than delete orders if customer-scoped keys cannot fit the old schema.
    duplicate = (
        op.get_bind()
        .execute(sa.text("SELECT idempotency_key FROM orders GROUP BY idempotency_key HAVING COUNT(*) > 1"))
        .first()
    )
    if duplicate:
        raise RuntimeError("Cannot restore global idempotency uniqueness: duplicate keys across customers")
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("uq_orders_customer_idempotency_key", type_="unique")
        batch.create_unique_constraint("uq_orders_idempotency_key", ["idempotency_key"])
        batch.drop_column("request_hash")
