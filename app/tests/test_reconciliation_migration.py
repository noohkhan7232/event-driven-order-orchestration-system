import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.models.entities import DEFAULT_TENANT_ID, Order, Payment, PaymentReconciliation
from app.models.enums import OrderStatus, PaymentStatus


def roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = sa.create_engine(url, hide_parameters=True)
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "0016_expiry_index")
        with Session(engine) as db:
            order = Order(
                tenant_id=DEFAULT_TENANT_ID,
                principal_scope="tenant",
                idempotency_key="legacy-poll",
                status=OrderStatus.PAYMENT_PENDING,
                total_amount=10,
            )
            db.add(order)
            db.flush()
            payment = Payment(order_id=order.id, provider="mock", amount=10, provider_reference="order_legacy")
            db.add(payment)
            db.commit()
            order_id, payment_id = order.id, payment.id
        command.upgrade(config, "head")
        command.downgrade(config, "0016_expiry_index")
        command.upgrade(config, "head")
        with Session(engine) as db:
            assert db.get(Payment, payment_id).status == PaymentStatus.INITIATED
            assert db.get(Order, order_id).total_amount == 10
            db.add(
                PaymentReconciliation(
                    payment_id=payment_id, order_id=order_id, tenant_id=DEFAULT_TENANT_ID, claim_token=uuid4()
                )
            )
            db.commit()
        with pytest.raises(RuntimeError, match="polling checkpoints require explicit archival"):
            command.downgrade(config, "0016_expiry_index")
        with Session(engine) as db:
            assert db.get(PaymentReconciliation, payment_id).claim_token is not None
            assert db.get(Payment, payment_id).provider_reference == "order_legacy"
        print("POLL CHECKPOINT LEGACY ROUNDTRIP PASS; OPERATIONAL DOWNGRADE REFUSED; FINANCIAL ROWS PRESERVED")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_payment_poll_migration(tmp_path, monkeypatch):
    roundtrip(f"sqlite:///{tmp_path / 'poll.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_payment_poll_migration(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    schema = "poll_" + uuid4().hex
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
