import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from legacy_orders import seed_legacy_order
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import get_settings
from app.models.entities import DEFAULT_TENANT_ID, FailedJob, Payment, Refund
from app.models.enums import OrderStatus, PaymentStatus


def roundtrip(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = sa.create_engine(url, hide_parameters=True)
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "0013_refunds")
        with Session(engine) as db:
            order = seed_legacy_order(
                db,
                tenant_id=DEFAULT_TENANT_ID,
                principal_scope="tenant",
                idempotency_key="legacy-claim",
                status=OrderStatus.CANCELLED,
                total_amount=Decimal("10.00"),
            )
            db.flush()
            payment = Payment(order_id=order.id, provider="mock", amount=10, status=PaymentStatus.AUTHORIZED)
            db.add(payment)
            db.flush()
            order_id, payment_id = order.id, payment.id
            db.commit()
        identity = uuid4()
        legacy = sa.Table(
            "refunds",
            sa.MetaData(),
            *(
                sa.Column(name, sa.Uuid(), primary_key=name == "id")
                for name in ("id", "tenant_id", "order_id", "payment_id")
            ),
            autoload_with=engine,
        )
        with engine.begin() as connection:
            connection.execute(
                legacy.insert().values(
                    id=identity,
                    tenant_id=DEFAULT_TENANT_ID,
                    order_id=order_id,
                    payment_id=payment_id,
                    amount=10,
                    currency="INR",
                    reason="ORDER_CANCELLED",
                    status="requested",
                    attempts=0,
                    idempotency_key="legacy-claim-key",
                )
            )
        command.upgrade(config, "head")
        command.downgrade(config, "0013_refunds")
        command.upgrade(config, "head")
        with Session(engine) as db:
            row = db.get(Refund, identity)
            assert row.amount == Decimal("10.00") and row.status == "requested"
            assert row.payment_id == payment_id and row.claim_token is None and row.failed_job_id is None
            job = FailedJob(task_name="refund-migration", queue_name="payments", payload={}, error_message="test")
            db.add(job)
            db.commit()
            for column, value in (
                ("claim_token", uuid4()),
                ("lease_until", datetime.now(UTC)),
                ("failed_job_id", job.id),
            ):
                setattr(row, column, value)
                db.commit()
                with pytest.raises(RuntimeError, match="claim/failure metadata requires archival"):
                    command.downgrade(config, "0013_refunds")
                db.expire_all()
                assert getattr(row, column) is not None and row.amount == Decimal("10.00")
                assert db.get(Payment, payment_id).status == PaymentStatus.AUTHORIZED
                setattr(row, column, None)
                db.commit()
        print("REFUND CLAIM ROUNDTRIP PASS; LEGACY MONEY PRESERVED; ALL THREE METADATA DOWNGRADES REFUSED")
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_sqlite_refund_claim_migration(tmp_path, monkeypatch):
    roundtrip(f"sqlite:///{tmp_path / 'claims.db'}", monkeypatch)


@pytest.mark.postgres
def test_postgres_refund_claim_migration(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    schema = "claims_" + uuid4().hex
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
