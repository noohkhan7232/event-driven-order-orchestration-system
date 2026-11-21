"""Durable refund attempts release every database lock before provider I/O."""

import math
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.principal import aware
from app.core.config import Settings, get_settings
from app.core.exceptions import BusinessError, DomainError, TransientError
from app.models.entities import AuditLog, FailedJob, Payment, Refund
from app.models.enums import JobStatus
from app.providers.contracts import (
    PaymentAttempt,
    PaymentProviderV2,
    ProviderDeclined,
    ProviderRefund,
    ProviderUnavailable,
    minor_units,
)
from app.providers.resolution import resolve_refund_provider
from app.repositories.orders import OrderRepository
from app.services.refund_intents import succeed_refund

SessionFactory = Callable[[], AbstractContextManager[Session]]


class RefundBusy(Exception):
    def __init__(self, seconds: int) -> None:
        self.seconds = seconds
        super().__init__("Refund attempt already claimed")


class RefundExhausted(BusinessError):
    code = "refund_attempts_exhausted"


@dataclass(frozen=True)
class Claim:
    refund_id: UUID
    token: UUID
    payment: PaymentAttempt
    amount_minor: int
    key: str
    reference: str | None
    provider: PaymentProviderV2 | None


def locked_refund(db: Session, identity: UUID) -> tuple[Refund, Payment] | None:
    initial = db.get(Refund, identity)
    if initial is None:
        return None
    order = OrderRepository(db).get_with_details(initial.order_id, tenant_id=initial.tenant_id, for_update=True)
    if order is None:
        raise TransientError("Refund order unavailable")
    row = db.scalars(
        select(Refund)
        .where(Refund.id == identity, Refund.tenant_id == order.tenant_id, Refund.order_id == order.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    payment = next((p for p in order.payments if p.id == row.payment_id), None)
    if payment is None or payment.provider == "cod":
        raise BusinessError("Invalid refund payment identity")
    return row, payment


def fail_refund(db: Session, row: Refund, label: str) -> None:
    if row.status == "succeeded" or row.failed_job_id is not None:
        return
    row.status, row.last_error = "failed", label
    row.claim_token, row.lease_until = None, None
    db.add(AuditLog(entity_type="refund", entity_id=row.id, action="refund_failed", payload={"reason": label}))
    if row.failed_job_id is None:
        job = FailedJob(
            tenant_id=row.tenant_id,
            task_name="app.workers.tasks.process_refund",
            queue_name="payments",
            aggregate_id=row.id,
            status=JobStatus.FAILED,
            payload={"refund_id": str(row.id)},
            error_message=label,
            retry_count=max(0, row.attempts - 1),
        )
        db.add(job)
        db.flush()
        row.failed_job_id = job.id


def claim_refund(db: Session, identity: UUID, settings: Settings) -> Claim | None:
    pair = locked_refund(db, identity)
    if pair is None:
        db.rollback()
        return None
    row, payment = pair
    if row.status in {"succeeded", "failed"}:
        db.commit()
        return None
    now = aware(db.scalar(select(func.now())) or datetime.now(UTC))
    if row.claim_token is not None and row.lease_until is not None and aware(row.lease_until) > now:
        seconds = max(1, math.ceil((aware(row.lease_until) - now).total_seconds()))
        db.rollback()
        raise RefundBusy(seconds)
    row.attempts += 1
    amount = minor_units(row.amount)
    try:
        provider = resolve_refund_provider(db, row.tenant_id, payment.provider, settings) if amount else None
        if amount and payment.provider == "razorpay" and payment.provider_transaction_id is None:
            raise BusinessError("Missing captured payment identity")
    except (DomainError, ValueError):
        fail_refund(db, row, "refund_configuration_unavailable")
        db.commit()
        raise RefundExhausted("Refund provider configuration unavailable") from None
    token = uuid4()
    row.claim_token, row.lease_until, row.status = (
        token,
        now + timedelta(seconds=settings.refund_lease_seconds),
        "processing",
    )
    snapshot = Claim(
        row.id,
        token,
        PaymentAttempt(payment.id, row.attempts - 1, payment.provider_transaction_id),
        amount,
        row.idempotency_key,
        row.provider_refund_id,
        provider,
    )
    db.commit()
    return snapshot


def provider_failed(factory: SessionFactory, claim: Claim, settings: Settings, permanent: bool) -> None:
    with factory() as db:
        pair = locked_refund(db, claim.refund_id)
        if pair is None:
            return
        row, _ = pair
        if row.status in {"succeeded", "failed"} or row.claim_token != claim.token:
            db.commit()
            return
        exhausted = row.attempts >= settings.task_max_retries + 1
        if permanent or exhausted:
            fail_refund(db, row, "provider_declined" if permanent else "refund_attempts_exhausted")
        else:
            row.status, row.last_error = "requested", "provider_unavailable"
            row.claim_token, row.lease_until = None, None
        db.commit()
    if permanent:
        raise ProviderDeclined("Refund declined") from None
    if exhausted:
        raise RefundExhausted("Refund attempts exhausted") from None
    raise ProviderUnavailable("Refund provider temporarily unavailable") from None


def call_provider(claim: Claim) -> ProviderRefund:
    provider = claim.provider
    if provider is None:
        raise ProviderDeclined("Refund provider unavailable")
    result: ProviderRefund | None
    if claim.reference is not None:
        result = provider.fetch_refund(claim.reference)
    elif claim.payment.retry_attempt > 0:
        result = provider.find_refund(claim.payment, claim.amount_minor, claim.key)
    else:
        result = None
    if result is None:
        result = provider.refund(claim.payment, claim.amount_minor, claim.key)
    if (
        not isinstance(result.provider_refund_id, str)
        or not result.provider_refund_id
        or result.status not in {"processed", "failed", "pending", "created", "processing"}
    ):
        raise ProviderUnavailable("Invalid refund response")
    return result


def run_refund(factory: SessionFactory, identity: UUID) -> None:
    settings = get_settings()
    with factory() as db:
        claim = claim_refund(db, identity, settings)
    if claim is None:
        return
    try:
        result = call_provider(claim) if claim.amount_minor else None
    except ProviderDeclined:
        provider_failed(factory, claim, settings, True)
        return
    except Exception:
        provider_failed(factory, claim, settings, False)
        return
    try:
        with factory() as db:
            pair = locked_refund(db, identity)
            if pair is None:
                return
            row, payment = pair
            if row.status == "succeeded":
                db.commit()
                return
            if result is None or result.status == "processed":
                reference = result.provider_refund_id if result else None
                if row.provider_refund_id is not None and row.provider_refund_id != reference:
                    raise TransientError("Refund reference conflict")
                succeed_refund(db, row, payment, reference)
            elif row.claim_token != claim.token:
                db.commit()
                return
            elif result.status == "failed":
                row.provider_refund_id = result.provider_refund_id
                fail_refund(db, row, "provider_refund_failed")
            else:
                row.provider_refund_id = result.provider_refund_id
                row.status, row.last_error = "processing", None
                row.claim_token, row.lease_until = None, None
            db.commit()
    except Exception:
        raise TransientError("Refund state commit unavailable") from None


def record_refund_failure(db: Session, identity: UUID) -> None:
    pair = locked_refund(db, identity)
    if pair:
        row, _ = pair
        if row.status != "succeeded":
            fail_refund(db, row, "refund_worker_failed")
    db.commit()
