import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.models.entities import DEFAULT_TENANT_ID, WebhookDelivery, WebhookEndpoint


def roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    engine = sa.create_engine(url, hide_parameters=True)
    try:
        command.upgrade(config, "0018_manual_shipping")
        command.upgrade(config, "head")
        command.downgrade(config, "0018_manual_shipping")
        command.upgrade(config, "head")
        constraint = next(
            row
            for row in sa.inspect(engine).get_unique_constraints("webhook_deliveries")
            if row["name"] == "uq_webhook_endpoint_event"
        )
        assert constraint["column_names"] == ["endpoint_id", "event_id"]
        with Session(engine) as db:
            endpoint = WebhookEndpoint(
                tenant_id=DEFAULT_TENANT_ID,
                url="https://seller.example/hook",
                secret_enc="synthetic-ciphertext",
                events=["ping"],
            )
            db.add(endpoint)
            db.flush()
            delivery = WebhookDelivery(
                tenant_id=DEFAULT_TENANT_ID,
                endpoint_id=endpoint.id,
                event_id=uuid4(),
                event_type="ping",
                payload={"data": {}},
                attempts=2,
                claim_token=uuid4(),
            )
            db.add(delivery)
            db.commit()
            identity = delivery.id
        with pytest.raises(RuntimeError, match="operational webhook"):
            command.downgrade(config, "0018_manual_shipping")
        with Session(engine) as db:
            preserved = db.get(WebhookDelivery, identity)
            assert preserved.attempts == 2 and preserved.claim_token is not None
            assert db.get(WebhookEndpoint, preserved.endpoint_id).secret_enc == "synthetic-ciphertext"
        print("OUTBOUND WEBHOOK EMPTY ROUNDTRIP PASS; OPERATIONAL CONFIG/DELIVERY/CLAIM DOWNGRADE REFUSED WITHOUT LOSS")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_outbound_webhook_migration(tmp_path, monkeypatch):
    roundtrip(f"sqlite:///{tmp_path / 'outbound.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_outbound_webhook_migration(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    engine = sa.create_engine(url)
    schema = "outbound_" + uuid4().hex
    with engine.begin() as conn:
        conn.execute(sa.text(f"CREATE SCHEMA {schema}"))
    try:
        scoped = sa.engine.make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
        roundtrip(scoped.render_as_string(hide_password=False), monkeypatch)
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()
