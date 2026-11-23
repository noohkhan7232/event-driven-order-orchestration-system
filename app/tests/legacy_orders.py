"""Seed orders against the actual historical schema, not today's ORM columns."""

from types import SimpleNamespace
from uuid import uuid4

import sqlalchemy as sa


def seed_legacy_order(db, **values):
    table = sa.Table(
        "orders",
        sa.MetaData(),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid()),
        autoload_with=db.get_bind(),
    )
    identity = uuid4()
    status = values.get("status")
    if hasattr(status, "name"):
        values["status"] = status.name
    values.setdefault("currency", "USD")
    db.execute(table.insert().values(id=identity, **values))
    return SimpleNamespace(id=identity)
