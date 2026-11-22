"""Leased at-least-once HTTP delivery. No live session crosses the I/O boundary."""

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.crypto import CredentialsUnavailable, CredentialVault
from app.core.ssrf import UnsafeTarget, resolve_target
from app.models.entities import Tenant, WebhookDelivery, WebhookEndpoint
from app.services.payment_timing import database_now


@dataclass(frozen=True)
class DeliveryClaim:
    id: UUID
    endpoint_id: UUID
    tenant_id: UUID
    token: UUID
    event_id: UUID
    event_type: str
    payload: dict
    url: str
    secret_enc: str
    exhausted: bool = False


def failure(endpoint: WebhookEndpoint, now, settings: Settings) -> None:
    endpoint.consecutive_failures += 1
    if endpoint.consecutive_failures >= settings.webhook_disable_after_failures:
        endpoint.is_active = False
        endpoint.disabled_at = now
        endpoint.disabled_reason = "consecutive_delivery_failures"


def claim_delivery(factory: sessionmaker[Session], settings: Settings) -> DeliveryClaim | None:
    with factory() as db:
        now = database_now(db)
        candidate = db.execute(
            select(WebhookEndpoint, WebhookDelivery)
            .join(WebhookDelivery, WebhookDelivery.endpoint_id == WebhookEndpoint.id)
            .join(Tenant, Tenant.id == WebhookEndpoint.tenant_id)
            .where(
                Tenant.status == "active",
                WebhookEndpoint.is_active.is_(True),
                WebhookEndpoint.deleted_at.is_(None),
                WebhookDelivery.tenant_id == WebhookEndpoint.tenant_id,
                WebhookDelivery.status.in_(["pending", "failed"]),
                WebhookDelivery.next_attempt_at <= now,
                or_(
                    WebhookDelivery.claim_token.is_(None),
                    WebhookDelivery.lease_until.is_(None),
                    WebhookDelivery.lease_until <= now,
                ),
            )
            .order_by(WebhookDelivery.next_attempt_at, WebhookDelivery.id)
            .with_for_update(of=WebhookEndpoint, skip_locked=True)
            .limit(1)
        ).first()
        if candidate is None:
            db.rollback()
            return None
        endpoint, selected = candidate
        delivery = db.scalar(
            select(WebhookDelivery)
            .where(WebhookDelivery.id == selected.id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if delivery is None:
            db.rollback()
            return None
        token = uuid4()
        exhausted = delivery.attempts >= settings.webhook_max_attempts
        if exhausted:
            delivery.status, delivery.last_error = "dead", "attempt_budget_exhausted"
            delivery.claim_token = delivery.lease_until = None
            failure(endpoint, now, settings)
        else:
            delivery.attempts += 1
            delivery.claim_token = token
            delivery.lease_until = now + timedelta(seconds=settings.webhook_lease_seconds)
        claim = DeliveryClaim(
            delivery.id,
            endpoint.id,
            endpoint.tenant_id,
            token,
            delivery.event_id,
            delivery.event_type,
            dict(delivery.payload),
            endpoint.url,
            endpoint.secret_enc,
            exhausted,
        )
        db.commit()
        return claim


def send_webhook(url: str, body: bytes, headers: dict[str, str], settings: Settings) -> int:
    url, address = resolve_target(url, settings)
    original = httpx.URL(url)
    pinned = original.copy_with(host=address)
    headers = {**headers, "Host": original.netloc.decode("ascii")}
    # httpcore's documented sni_hostname extension preserves certificate hostname
    # verification while the socket connects only to the validated numeric peer.
    # No ambient proxies, redirects, or second hostname DNS lookup.
    with httpx.Client(timeout=settings.webhook_timeout_seconds, follow_redirects=False, trust_env=False) as client:
        with client.stream(
            "POST", pinned, content=body, headers=headers, extensions={"sni_hostname": original.host}
        ) as response:
            if not response.is_stream_consumed:
                consumed = 0
                for chunk in response.iter_raw(chunk_size=min(1024, settings.webhook_response_max_bytes)):
                    consumed += len(chunk)
                    if consumed >= settings.webhook_response_max_bytes:
                        break
            return response.status_code


def signed_request(claim: DeliveryClaim, settings: Settings) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(claim.payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode()
    timestamp = str(int(time.time()))
    secret = CredentialVault(settings.credentials_encryption_key).decrypt(claim.secret_enc)
    digest = hmac.new(secret.get_secret_value().encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "User-Agent": "oos-webhooks/1",
        "X-OOS-Event-Id": str(claim.event_id),
        "X-OOS-Event-Type": claim.event_type,
        "X-OOS-Delivery-Id": str(claim.id),
        "X-OOS-Timestamp": timestamp,
        "X-OOS-Signature": "t=" + timestamp + ",v1=" + digest,
    }


def finish_delivery(
    factory: sessionmaker[Session], settings: Settings, claim: DeliveryClaim, code: int | None, error: str | None
) -> str:
    with factory() as db:
        endpoint = db.scalar(
            select(WebhookEndpoint)
            .where(WebhookEndpoint.id == claim.endpoint_id, WebhookEndpoint.tenant_id == claim.tenant_id)
            .with_for_update()
        )
        delivery = db.scalar(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.id == claim.id,
                WebhookDelivery.endpoint_id == claim.endpoint_id,
                WebhookDelivery.tenant_id == claim.tenant_id,
            )
            .with_for_update()
        )
        if endpoint is None or delivery is None or delivery.claim_token != claim.token:
            db.rollback()
            return "stale"
        now = database_now(db)
        delivery.last_status_code, delivery.last_error = code, error
        delivery.claim_token = delivery.lease_until = None
        if error is None:
            delivery.status, delivery.delivered_at = "delivered", now
            endpoint.consecutive_failures = 0
        else:
            delivery.status = "dead" if delivery.attempts >= settings.webhook_max_attempts else "failed"
            delay = min(
                settings.webhook_retry_max_seconds,
                settings.webhook_retry_base_seconds * 2 ** min(delivery.attempts - 1, 30),
            )
            jitter = 1 + secrets.randbelow(1001) / 10000
            delivery.next_attempt_at = now + timedelta(seconds=delay * jitter)
            failure(endpoint, now, settings)
        result = delivery.status
        db.commit()
        return result


def run_delivery_batch(factory: sessionmaker[Session], settings: Settings) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _ in range(settings.webhook_delivery_batch_size):
        claim = claim_delivery(factory, settings)
        if claim is None:
            break
        if claim.exhausted:
            result = "dead"
        else:
            code = None
            try:
                body, headers = signed_request(claim, settings)
                code = send_webhook(claim.url, body, headers, settings)
                error = None if 200 <= code < 300 else "http_status"
            except UnsafeTarget:
                error = "unsafe_target"
            except CredentialsUnavailable:
                error = "credentials_unavailable"
            except (httpx.HTTPError, OSError, ValueError):
                error = "transport_error"
            result = finish_delivery(factory, settings, claim, code, error)
        counts[result] = counts.get(result, 0) + 1
    return counts
