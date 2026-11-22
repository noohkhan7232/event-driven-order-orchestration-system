import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.models.entities import DEFAULT_TENANT_ID, InboundWebhookEvent, OutboxEvent, Tenant
from app.services.inbound_payments import stage_receipt


def test_same_event_id_is_independent_per_tenant_and_locally_deduplicated(db):
    tenant = Tenant(name="Independent seller", slug="independent-receipt")
    db.add(tenant)
    db.commit()
    first = stage_receipt(db, "razorpay", DEFAULT_TENANT_ID, "evt_shared", [])
    db.commit()
    second = stage_receipt(db, "razorpay", tenant.id, "evt_shared", [])
    db.commit()
    assert first != second
    assert stage_receipt(db, "razorpay", DEFAULT_TENANT_ID, "evt_shared", []) == first
    assert stage_receipt(db, "razorpay", tenant.id, "evt_shared", []) == second
    db.commit()
    assert db.query(InboundWebhookEvent).count() == db.query(OutboxEvent).count() == 2
    assert {row.tenant_id for row in db.query(InboundWebhookEvent)} == {DEFAULT_TENANT_ID, tenant.id}
    assert {row.tenant_id for row in db.query(OutboxEvent)} == {DEFAULT_TENANT_ID, tenant.id}


def roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = sa.create_engine(url, hide_parameters=True)
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "0014_refund_claims")
        tenants = sa.Table("tenants", sa.MetaData(), sa.Column("id", sa.Uuid(), primary_key=True), autoload_with=engine)
        other_tenant = uuid4()
        with Session(engine) as db:
            db.execute(
                tenants.insert().values(
                    id=other_tenant,
                    name="Second seller",
                    slug="receipt-migration",
                    status="active",
                    default_currency="INR",
                )
            )
            row = InboundWebhookEvent(
                tenant_id=DEFAULT_TENANT_ID,
                provider="razorpay",
                provider_event_id="evt_legacy",
                event_type="ignored",
                payload={"events": []},
                status="ignored",
            )
            db.add(row)
            db.commit()
            identity, received = row.id, row.received_at
        command.upgrade(config, "head")
        constraint = next(
            c
            for c in sa.inspect(engine).get_unique_constraints("inbound_webhook_events")
            if c["name"] == "uq_inbound_provider_event"
        )
        assert constraint["column_names"] == ["provider", "tenant_id", "provider_event_id"]
        command.downgrade(config, "0014_refund_claims")
        command.upgrade(config, "head")
        with Session(engine) as db:
            preserved = db.get(InboundWebhookEvent, identity)
            assert preserved.received_at == received and preserved.status == "ignored"
            second = stage_receipt(db, "razorpay", other_tenant, "evt_legacy", [])
            db.commit()
        with pytest.raises(RuntimeError, match="cross-tenant event ID conflicts"):
            command.downgrade(config, "0014_refund_claims")
        with Session(engine) as db:
            assert db.get(InboundWebhookEvent, identity).provider_event_id == "evt_legacy"
            assert db.get(InboundWebhookEvent, second).tenant_id == other_tenant
            assert db.query(InboundWebhookEvent).count() == 2
            assert db.query(OutboxEvent).count() == 1
        print(
            "TENANT RECEIPT LEGACY ROUNDTRIP PASS; CONFLICTING DOWNGRADE REFUSED BEFORE DDL; RECEIPTS/INTENT PRESERVED"
        )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_tenant_receipt_migration(tmp_path, monkeypatch):
    roundtrip(f"sqlite:///{tmp_path / 'tenant-receipt.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_tenant_receipt_migration(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    schema = "tenant_receipt_" + uuid4().hex
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
