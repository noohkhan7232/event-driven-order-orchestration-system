import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.models.entities import DEFAULT_TENANT_ID, ApiKey


def roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    engine = sa.create_engine(url)
    try:
        command.upgrade(config, "0007_tenancy")
        with Session(engine) as db:
            key = ApiKey(
                tenant_id=DEFAULT_TENANT_ID,
                name="Legacy",
                key_prefix="oos_live_abc",
                key_hash="0" * 64,
                scopes=["orders:read"],
            )
            db.add(key)
            db.commit()
            identity = key.id
        command.upgrade(config, "head")
        assert (
            next(c for c in sa.inspect(engine).get_columns("api_keys") if c["name"] == "key_prefix")["type"].length
            == 24
        )
        with Session(engine) as db:
            assert db.get(ApiKey, identity).key_hash == "0" * 64
        command.downgrade(config, "0007_tenancy")
        command.upgrade(config, "head")
        with Session(engine) as db:
            db.add(
                ApiKey(
                    tenant_id=DEFAULT_TENANT_ID,
                    name="Modern",
                    key_prefix="oos_live_abcdefgh",
                    key_hash="1" * 64,
                    scopes=[],
                )
            )
            db.commit()
        with pytest.raises(RuntimeError, match="17-character"):
            command.downgrade(config, "0007_tenancy")
        print("LEGACY PREFIX PRESERVED; 0007/HEAD ROUNDTRIP PASS; NEW PREFIX DOWNGRADE REFUSED")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_prefix_migration(tmp_path, monkeypatch):
    roundtrip(f"sqlite:///{tmp_path / 'prefix.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_prefix_migration(monkeypatch):
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["TEST_DATABASE_URL"])
    engine = sa.create_engine(url)
    schema = "prefix_" + uuid4().hex
    with engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    try:
        roundtrip(
            url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False),
            monkeypatch,
        )
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()
