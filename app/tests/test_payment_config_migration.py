import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from cryptography.fernet import Fernet

from alembic import command
from app.core.config import get_settings
from app.models.entities import DEFAULT_TENANT_ID


def roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    engine = sa.create_engine(url)
    try:
        command.upgrade(config, "0009_timeline")
        command.upgrade(config, "head")
        assert "tenant_payment_configs" in sa.inspect(engine).get_table_names()
        command.downgrade(config, "0009_timeline")
        command.upgrade(config, "head")
        table = sa.Table(
            "tenant_payment_configs",
            sa.MetaData(),
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("tenant_id", sa.Uuid()),
            autoload_with=engine,
        )
        encrypted = Fernet(Fernet.generate_key()).encrypt(b"synthetic-credential").decode()
        with engine.begin() as connection:
            connection.execute(
                table.insert().values(
                    id=uuid4(),
                    tenant_id=DEFAULT_TENANT_ID,
                    provider="razorpay",
                    mode="test",
                    key_id="public1234",
                    key_secret_enc=encrypted,
                    webhook_secret_enc=encrypted,
                    webhook_public_id=uuid4().hex,
                    is_active=True,
                )
            )
        with pytest.raises(RuntimeError, match="credentials require explicit archival"):
            command.downgrade(config, "0009_timeline")
        with engine.connect() as connection:
            assert connection.scalar(sa.select(table.c.key_secret_enc)) == encrypted
        print("PAYMENT CONFIG EMPTY ROUNDTRIP PASS; ENCRYPTED CREDENTIALS DOWNGRADE REFUSED AND PRESERVED")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_payment_config_migration(tmp_path, monkeypatch):
    roundtrip(f"sqlite:///{tmp_path / 'config.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_payment_config_migration(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    schema = "config_" + uuid4().hex
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(sa.text(f"CREATE SCHEMA {schema}"))
    try:
        scoped = sa.engine.make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
        roundtrip(scoped.render_as_string(hide_password=False), monkeypatch)
    finally:
        with engine.begin() as connection:
            connection.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()
