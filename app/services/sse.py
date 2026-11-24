"""Bounded timeline streams; every poll closes its transaction before yielding."""

import asyncio
import hashlib
import json
import math
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import anyio
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.auth.principal import aware
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.ratelimit import BackendUnavailable, Decision, RateLimiter, RateLimitError, get_limiter
from app.models.entities import ApiKey, Order, OrderTimeline, Tenant, User
from app.models.enums import UserRole
from app.services.payment_timing import database_now
from app.services.tracking import FRIENDLY, PUBLIC_HEADERS


@dataclass(frozen=True)
class StreamScope:
    tenant_id: UUID
    order_id: UUID | None = None
    customer_id: UUID | None = None
    tracking_version: int | None = None
    user_id: UUID | None = None
    key_id: UUID | None = None

    def active(self, db: Session, now: datetime) -> bool:
        tenant = db.get(Tenant, self.tenant_id)
        if tenant is None or tenant.status != "active":
            return False
        if self.tracking_version is not None:
            return (
                db.scalar(
                    select(Order.id).where(
                        Order.id == self.order_id,
                        Order.tenant_id == self.tenant_id,
                        Order.tracking_version == self.tracking_version,
                    )
                )
                is not None
            )
        if self.user_id:
            user = db.get(User, self.user_id)
            return bool(
                user
                and user.is_active
                and user.tenant_id == self.tenant_id
                and (user.role != UserRole.CUSTOMER or self.customer_id == user.id)
            )
        if self.key_id:
            key = db.get(ApiKey, self.key_id)
            return bool(
                key
                and key.tenant_id == self.tenant_id
                and key.revoked_at is None
                and (key.expires_at is None or aware(key.expires_at) > now)
                and "orders:read" in key.scopes
            )
        return True


@dataclass(frozen=True)
class TimelineEvent:
    id: int
    order_id: UUID
    from_status: str | None
    to_status: str
    at: datetime

    def frame(self, public: bool) -> str:
        previous, following = self.from_status, self.to_status
        if public:
            previous = FRIENDLY.get(previous, "processing") if previous else None
            following = FRIENDLY.get(following, "processing")
        payload = {"order_id": str(self.order_id), "from": previous, "to": following, "at": aware(self.at).isoformat()}
        return f"id: {self.id}\nevent: order.status_changed\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


class TimelineReader:
    def __init__(
        self, factory: sessionmaker[Session], scope: StreamScope, settings: Settings, cursor: int | None = None
    ):
        self.factory, self.scope, self.settings = factory, scope, settings
        self.pending: dict[int, TimelineEvent] = {}
        self.safe_id: int | None = None
        with factory() as db:
            now = database_now(db)
            self.ceiling = db.scalar(self.statement().with_only_columns(func.max(OrderTimeline.id))) or 0
            self.after = self.ceiling if cursor is None else cursor
            # Skip already-visible rows at/before the cursor. A later commit in
            # this recent window is still discovered, even below the high ID.
            self.seen = set(
                db.scalars(
                    self.statement()
                    .with_only_columns(OrderTimeline.id)
                    .where(
                        OrderTimeline.id <= self.after,
                        OrderTimeline.created_at >= now - timedelta(seconds=settings.sse_lookback_seconds * 2),
                    )
                    .limit(settings.sse_max_events + 1)
                )
            )
            if len(self.seen) > settings.sse_max_events:
                raise RateLimitError()

    def statement(self):
        scope = self.scope
        statement = (
            select(OrderTimeline)
            .join(Order, Order.id == OrderTimeline.order_id)
            .where(OrderTimeline.tenant_id == scope.tenant_id, Order.tenant_id == scope.tenant_id)
        )
        if scope.order_id:
            statement = statement.where(Order.id == scope.order_id)
        if scope.customer_id:
            statement = statement.where(Order.customer_id == scope.customer_id)
        return statement

    def poll(self) -> list[TimelineEvent] | None:
        with self.factory() as db:
            now = database_now(db)
            if not self.scope.active(db, now):
                return None
            room = self.settings.sse_max_events - len(self.seen)
            if room > 0:
                batch = min(room, self.settings.sse_batch_size)
                rows = list(
                    db.scalars(
                        self.statement()
                        .where(
                            or_(
                                OrderTimeline.id > self.after,
                                (OrderTimeline.id <= self.ceiling)
                                & (
                                    OrderTimeline.created_at
                                    >= now - timedelta(seconds=self.settings.sse_lookback_seconds * 2)
                                ),
                            ),
                            OrderTimeline.id.not_in(self.seen),
                        )
                        .order_by(OrderTimeline.id)
                        .limit(batch + 1)
                    )
                )
                self.safe_id = rows[batch - 1].id if len(rows) > batch else None
                for row in rows[:batch]:
                    self.seen.add(row.id)
                    self.pending[row.id] = TimelineEvent(
                        row.id, row.order_id, row.from_status, row.to_status, aware(row.created_at)
                    )
            cutoff = now - timedelta(seconds=self.settings.sse_lookback_seconds)
            ready = []
            # Do not overtake an earlier pending observation still in holdback.
            for identity in sorted(self.pending):
                if self.pending[identity].at > cutoff or (self.safe_id is not None and identity > self.safe_id):
                    break
                ready.append(self.pending.pop(identity))
            return ready

    async def events(self) -> AsyncIterator[str]:
        start = last_heartbeat = time.monotonic()
        yield ": connected\n\n"
        while time.monotonic() - start < self.settings.sse_max_seconds:
            rows = await run_in_threadpool(self.poll)
            if rows is None:
                return
            for row in rows:
                yield row.frame(self.scope.tracking_version is not None)
            now = time.monotonic()
            if now - last_heartbeat >= self.settings.sse_heartbeat_seconds:
                yield ": heartbeat\n\n"
                last_heartbeat = now
            if len(self.seen) >= self.settings.sse_max_events and (not self.pending or self.safe_id is not None):
                return
            await asyncio.sleep(
                max(
                    0,
                    min(
                        self.settings.sse_poll_seconds,
                        self.settings.sse_max_seconds - (time.monotonic() - start),
                        self.settings.sse_heartbeat_seconds - (time.monotonic() - last_heartbeat),
                    ),
                )
            )


class Releasable(Protocol):
    def release(self) -> None: ...


@dataclass(frozen=True)
class StreamLease:
    backend: RateLimiter
    keys: tuple[tuple[str, int], ...]
    token: str

    def release(self) -> None:
        try:
            self.backend.release(self.keys, self.token)
        except BackendUnavailable:
            # The finite lease recovers a disconnect even when Redis is down.
            get_logger(__name__).warning("stream_lease_release_unavailable")


def acquire_stream(tenant_id: UUID, ip: str, settings: Settings) -> StreamLease:
    keys = tuple(
        (hashlib.sha256(f"{kind}:{value}".encode()).hexdigest(), limit)
        for kind, value, limit in (
            ("tenant", str(tenant_id), settings.sse_tenant_connection_limit),
            ("ip", ip, settings.sse_ip_connection_limit),
        )
    )
    backend = get_limiter(settings.rate_limit_redis_url, settings.environment in {"local", "development", "test"})
    token = uuid4().hex
    try:
        if not backend.acquire(keys, token, settings.sse_max_seconds + 10):
            raise RateLimitError(
                result=Decision(
                    False, min(limit for _, limit in keys), 0, math.ceil(time.time() + settings.sse_max_seconds), 1
                )
            )
    except BackendUnavailable:
        raise RateLimitError() from None
    return StreamLease(backend, keys, token)


class StreamResponse(StreamingResponse):
    def __init__(self, reader: TimelineReader, lease: Releasable):
        self.lease = lease
        self.maximum = reader.settings.sse_max_seconds
        super().__init__(
            reader.events(), media_type="text/event-stream", headers={**PUBLIC_HEADERS, "X-Accel-Buffering": "no"}
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = complete = False

        async def tracked_send(message):
            nonlocal started, complete
            await send(message)
            if message["type"] == "http.response.start":
                started = True
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                complete = True

        try:
            with anyio.move_on_after(self.maximum) as deadline:
                await super().__call__(scope, receive, tracked_send)
            if deadline.cancel_called and started and not complete:
                # Terminate a timed-out stream even when the client stopped
                # consuming a body chunk. Do not let finalization block forever.
                with anyio.move_on_after(1):
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            # Covers cancellation/send failure even before the generator starts.
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(self.lease.release)
