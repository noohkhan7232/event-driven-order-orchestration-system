import os
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.models.entities import DEFAULT_TENANT_ID, Order, Payment, Refund
from app.models.enums import OrderStatus, PaymentStatus


def roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = sa.create_engine(url, hide_parameters=True)
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "0012_inbound")
        with Session(engine) as db:
            order = Order(
                tenant_id=DEFAULT_TENANT_ID,
                principal_scope="tenant",
                idempotency_key="legacy-refund",
                status=OrderStatus.CANCELLED,
                total_amount=Decimal("10.00"),
                currency="INR",
            )
            db.add(order)
            db.flush()
            payment = Payment(
                order_id=order.id,
                provider="mock",
                status=PaymentStatus.AUTHORIZED,
                amount=Decimal("10.00"),
                provider_transaction_id="pay_legacy",
            )
            db.add(payment)
            db.commit()
            order_id, payment_id = order.id, payment.id
        command.upgrade(config, "head")
        command.downgrade(config, "0012_inbound")
        command.upgrade(config, "head")
        with Session(engine) as db:
            db.add(
                Refund(
                    tenant_id=DEFAULT_TENANT_ID,
                    order_id=order_id,
                    payment_id=payment_id,
                    amount=Decimal("10.00"),
                    currency="INR",
                    reason="ORDER_CANCELLED",
                    status="requested",
                    attempts=0,
                    idempotency_key="legacy-refund-key",
                )
            )
            db.commit()
        with pytest.raises(RuntimeError, match="durable refunds require explicit archival"):
            command.downgrade(config, "0012_inbound")
        with engine.connect() as connection:
            assert Decimal(str(connection.scalar(sa.text("SELECT amount FROM refunds")))) == Decimal("10.00")
            assert connection.scalar(sa.text("SELECT provider_transaction_id FROM payments")) == "pay_legacy"
        print("REFUND EMPTY ROUNDTRIP PASS; LEGACY PAYMENT AND DURABLE REQUEST PRESERVED")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_refund_migration(tmp_path, monkeypatch):
    roundtrip(f"sqlite:///{tmp_path / 'refund.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_refund_migration(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    schema = "refund_" + uuid4().hex
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
