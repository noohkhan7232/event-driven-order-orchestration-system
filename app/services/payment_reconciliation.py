"""Claim, release all transactions, fetch, then journal normalized outcomes."""

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import DomainError
from app.core.logging import get_logger
from app.events.contracts import DomainEvent
from app.models.entities import Order, Payment, PaymentReconciliation, Refund
from app.models.enums import OrderStatus, PaymentStatus
from app.providers.contracts import PaymentAttempt, PaymentProviderV2, ProviderEvent, ProviderUnavailable, minor_units
from app.providers.resolution import resolve_refund_provider
from app.repositories.orders import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.services.inbound_payments import stage_receipt
from app.services.payment_timing import database_now
from app.services.refunds import SessionFactory, fail_refund, locked_refund

logger = get_logger(__name__)
Kind = Literal["payment", "refund"]


@dataclass(frozen=True)
class PollClaim:
    kind: Kind
    identity: UUID
    tenant_id: UUID
    order_id: UUID
    token: UUID
    provider_name: str
    provider: PaymentProviderV2 | None
    payment: PaymentAttempt
    reference: str | None
    amount_minor: int
    currency: str
    key: str
    correlation_id: str | None


def claim_next(db: Session, kind: Kind, seen: set[UUID]) -> PollClaim | None:
    target: PaymentReconciliation | Refund
    settings = get_settings()
    now = database_now(db)
    cutoff = now - timedelta(seconds=settings.payment_reconcile_after_seconds)
    if kind == "payment":
        query = (
            select(Order, Payment.id)
            .join(Payment, Payment.order_id == Order.id)
            .outerjoin(PaymentReconciliation, PaymentReconciliation.payment_id == Payment.id)
            .where(
                Order.status == OrderStatus.PAYMENT_PENDING,
                Order.payment_method == "prepaid",
                Order.created_at <= cutoff,
                Payment.status == PaymentStatus.INITIATED,
                Payment.provider_reference.is_not(None),
                Payment.provider != "cod",
                or_(PaymentReconciliation.last_checked_at.is_(None), PaymentReconciliation.last_checked_at <= cutoff),
                or_(PaymentReconciliation.lease_until.is_(None), PaymentReconciliation.lease_until <= now),
            )
            .order_by(PaymentReconciliation.last_checked_at.asc().nulls_first(), Payment.id)
        )
        if seen:
            query = query.where(Payment.id.not_in(seen))
    else:
        query = (
            select(Order, Refund.id)
            .join(Refund, Refund.order_id == Order.id)
            .where(
                Refund.tenant_id == Order.tenant_id,
                Refund.status == "processing",
                Refund.updated_at <= cutoff,
                or_(Refund.lease_until.is_(None), Refund.lease_until <= now),
            )
            .order_by(Refund.updated_at, Refund.id)
        )
        if seen:
            query = query.where(Refund.id.not_in(seen))
    found = db.execute(
        query.limit(1).with_for_update(of=Order, skip_locked=True).execution_options(populate_existing=True)
    ).first()
    if found is None:
        db.rollback()
        return None
    order, identity = found
    if kind == "payment":
        payment = db.scalars(select(Payment).where(Payment.id == identity, Payment.order_id == order.id)).one()
        checkpoint = db.scalar(
            select(PaymentReconciliation)
            .where(PaymentReconciliation.payment_id == identity)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if checkpoint is None:
            checkpoint = PaymentReconciliation(payment_id=identity, order_id=order.id, tenant_id=order.tenant_id)
            db.add(checkpoint)
        if checkpoint.order_id != order.id or checkpoint.tenant_id != order.tenant_id:
            raise ProviderUnavailable("Invalid polling ownership")
        target = checkpoint
        reference, amount, currency, key, correlation = (
            payment.provider_reference,
            payment.amount,
            order.currency,
            "",
            None,
        )
        attempt = 0
    else:
        pair = locked_refund(db, identity)
        if pair is None:
            raise ProviderUnavailable("Polling refund unavailable")
        refund, payment = pair
        target = refund
        reference, amount, currency, key, correlation = (
            refund.provider_refund_id,
            refund.amount,
            refund.currency,
            refund.idempotency_key,
            refund.correlation_id,
        )
        attempt = refund.attempts
    token = uuid4()
    target.claim_token, target.lease_until = token, now + timedelta(seconds=settings.payment_reconcile_lease_seconds)
    try:
        provider = (
            resolve_refund_provider(db, order.tenant_id, payment.provider, settings)
            if amount or kind == "payment"
            else None
        )
    except DomainError:
        provider = None
    claim = PollClaim(
        kind,
        identity,
        order.tenant_id,
        order.id,
        token,
        payment.provider or "",
        provider,
        PaymentAttempt(payment.id, attempt, payment.provider_transaction_id),
        reference,
        minor_units(amount),
        currency,
        key,
        correlation,
    )
    db.commit()
    return claim


def synthetic_notice(claim: PollClaim, notice: ProviderEvent) -> ProviderEvent:
    identity = json.dumps(
        [
            claim.tenant_id.hex,
            claim.provider_name,
            notice.type,
            notice.provider_order_id,
            notice.provider_payment_id,
            notice.provider_refund_id,
        ],
        separators=(",", ":"),
    )
    return replace(notice, event_id="reconcile_" + hashlib.sha256(identity.encode()).hexdigest(), raw={})


def fetch_notices(claim: PollClaim) -> tuple[list[ProviderEvent], bool]:
    if claim.kind == "refund" and claim.amount_minor == 0:
        return [], True
    if claim.provider is None:
        raise ProviderUnavailable("Polling provider unavailable")
    if claim.kind == "payment":
        if claim.reference is None:
            raise ProviderUnavailable("Polling reference unavailable")
        notices = claim.provider.fetch_payment_status(claim.reference)
        if any(
            n.type not in {"payment_captured", "payment_failed"}
            or n.provider_order_id != claim.reference
            or type(n.amount_minor) is not int
            or n.amount_minor < 0
            or not isinstance(n.provider_payment_id, str)
            or not n.provider_payment_id
            or not isinstance(n.currency, str)
            or not re.fullmatch("[A-Z]{3}", n.currency)
            for n in notices
        ):
            raise ProviderUnavailable("Invalid polling response")
        return [synthetic_notice(claim, notice) for notice in notices], False
    result = (
        claim.provider.fetch_refund(claim.reference)
        if claim.reference
        else claim.provider.find_refund(claim.payment, claim.amount_minor, claim.key)
    )
    if result is None:
        return [], True
    if (
        not isinstance(result.provider_refund_id, str)
        or not result.provider_refund_id
        or (claim.reference and result.provider_refund_id != claim.reference)
        or result.status not in {"pending", "created", "processing", "processed", "failed"}
    ):
        raise ProviderUnavailable("Invalid refund polling response")
    if result.status in {"pending", "created", "processing"}:
        return [], False
    if claim.payment.provider_payment_id is None:
        raise ProviderUnavailable("Captured payment identity unavailable")
    notice = ProviderEvent(
        "poll",
        "refund_processed" if result.status == "processed" else "refund_failed",
        None,
        claim.payment.provider_payment_id,
        result.provider_refund_id,
        claim.amount_minor,
        claim.currency,
        {},
    )
    return [synthetic_notice(claim, notice)], False


def finish_poll(db: Session, claim: PollClaim, notices: list[ProviderEvent], retry_refund: bool, error: bool) -> None:
    target: PaymentReconciliation | Refund
    order = OrderRepository(db).get_with_details(claim.order_id, tenant_id=claim.tenant_id, for_update=True)
    if order is None:
        raise ProviderUnavailable("Polling order unavailable")
    if claim.kind == "payment":
        target = db.scalars(
            select(PaymentReconciliation)
            .where(
                PaymentReconciliation.payment_id == claim.identity, PaymentReconciliation.tenant_id == claim.tenant_id
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
    else:
        pair = locked_refund(db, claim.identity)
        if pair is None:
            raise ProviderUnavailable("Polling refund unavailable")
        target, _ = pair
    # Financial observations remain durable even after lease replacement or a
    # simultaneous expiry; only the current claimant may edit polling metadata.
    for notice in notices:
        stage_receipt(db, claim.provider_name, claim.tenant_id, notice.event_id, [notice], claim.correlation_id)
    if target.claim_token == claim.token:
        target.claim_token, target.lease_until = None, None
        target.last_error = "provider_unavailable" if error else None
        if isinstance(target, PaymentReconciliation):
            target.last_checked_at = database_now(db)
        elif retry_refund:
            if claim.amount_minor and target.attempts >= get_settings().task_max_retries + 1:
                fail_refund(db, target, "refund_attempts_exhausted")
            else:
                target.status = "requested"
                OutboxRepository(db).add(
                    DomainEvent(
                        event_type="refund.requested",
                        aggregate_id=target.id,
                        tenant_id=target.tenant_id,
                        correlation_id=target.correlation_id,
                        payload={"refund_id": str(target.id), "order_id": str(target.order_id)},
                    ),
                    "payments",
                )
        elif notices:
            target.provider_refund_id = notices[0].provider_refund_id
    db.commit()


def reconcile_payments(factory: SessionFactory) -> dict[str, int]:
    counts = {"payments": 0, "refunds": 0, "errors": 0}
    for kind in ("payment", "refund"):
        seen: set[UUID] = set()
        for _ in range(get_settings().payment_maintenance_batch_size):
            with factory() as db:
                try:
                    claim = claim_next(db, kind, seen)
                except Exception:
                    db.rollback()
                    raise
            if claim is None:
                break
            seen.add(claim.identity)
            error = False
            try:
                notices, retry_refund = fetch_notices(claim)
            except Exception:
                notices, retry_refund, error = [], False, True
                logger.warning("provider_reconciliation_unavailable", order_id=str(claim.order_id), kind=kind)
            with factory() as db:
                try:
                    finish_poll(db, claim, notices, retry_refund, error)
                except Exception:
                    db.rollback()
                    raise
            counts["payments" if kind == "payment" else "refunds"] += 1
            counts["errors"] += int(error)
    return counts
