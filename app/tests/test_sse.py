import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import TestingSessionLocal
from test_payment_config import identity

from app.core.config import get_settings
from app.main import app
from app.models.entities import Order, OrderTimeline, Tenant


@pytest.fixture
def stream_settings():
    settings = get_settings().model_copy(
        update={
            "sse_max_seconds": 0.08,
            "sse_poll_seconds": 0.005,
            "sse_heartbeat_seconds": 0.02,
            "sse_lookback_seconds": 0.01,
            "sse_batch_size": 2,
            "sse_max_events": 10000,
        }
    )
    app.dependency_overrides[get_settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(get_settings, None)


def timeline(db, order, to="paid", **values):
    row = OrderTimeline(
        tenant_id=order.tenant_id,
        order_id=order.id,
        from_status="payment_pending",
        to_status=to,
        reason="PRIVATE_REASON",
        created_at=values.pop("created_at", datetime.now(UTC) - timedelta(seconds=60)),
        **values,
    )
    db.add(row)
    db.commit()
    return row


def frames(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_stream_replay_resume_new_connection_privacy_and_heartbeat(client, db, workflow, stream_settings):
    order, _ = workflow
    _, auth = identity(db)
    first, second = timeline(db, order), timeline(db, order, "shipped")
    response = client.get("/api/v1/orders/stream?last_event_id=0", headers=auth)
    assert [row["to"] for row in frames(response)] == ["paid", "shipped"]
    assert f"id: {first.id}\n" in response.text and f"id: {second.id}\n" in response.text
    assert response.text.count("event: order.status_changed") == 2 and "PRIVATE_REASON" not in response.text
    assert frames(client.get("/api/v1/orders/stream", headers=auth)) == []
    resumed = client.get("/api/v1/orders/stream", headers={**auth, "Last-Event-ID": str(first.id)})
    assert [row["to"] for row in frames(resumed)] == ["shipped"]
    assert ": heartbeat\n\n" in resumed.text and resumed.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/orders/stream?last_event_id=-1", headers=auth).status_code == 422
    assert client.get("/api/v1/orders/stream", headers={**auth, "Last-Event-ID": "secret-invalid"}).status_code == 422
    assert client.get("/api/v1/orders/stream").status_code == 401


def test_public_stream_is_friendly_filtered_and_revocable(client, db, workflow, stream_settings):
    order, _ = workflow
    timeline(db, order)
    token = order.tracking_token
    response = client.get(f"/api/v1/public/track/{token}/stream?last_event_id=0")
    payload = frames(response)
    assert len(payload) == 1 and set(payload[0]) == {"order_id", "from", "to", "at"}
    assert payload[0] | {"at": "timestamp"} == {
        "order_id": str(order.id),
        "from": "processing",
        "to": "processing",
        "at": "timestamp",
    }
    assert response.headers["referrer-policy"] == "no-referrer"
    for forbidden in ("buyer", "reason", "payment_pending", "paid", "PRIVATE_REASON", "tenant_id", "token"):
        assert forbidden not in response.text
    _, auth = identity(db)
    client.post(f"/api/v1/orders/{order.id}/tracking/rotate", headers=auth)
    old = client.get(f"/api/v1/public/track/{token}/stream")
    invalid = client.get("/api/v1/public/track/bad/stream")
    assert old.status_code == invalid.status_code == 404 and old.json() == invalid.json()


def test_stream_scope_customer_filter_and_suspension(client, db, workflow, stream_settings):
    from app.auth.security import create_access_token

    order, _ = workflow
    timeline(db, order)
    outsider, auth = identity(db)
    other = Order(
        tenant_id=order.tenant_id,
        customer_id=outsider.id,
        principal_scope="test",
        idempotency_key=uuid4().hex,
        total_amount=10,
    )
    db.add(other)
    db.commit()
    timeline(db, other, "shipped")
    customer = {"Authorization": "Bearer " + create_access_token(str(order.customer_id))}
    assert [
        row["order_id"] for row in frames(client.get("/api/v1/orders/stream?last_event_id=0", headers=customer))
    ] == [str(order.id)]
    assert client.get(f"/api/v1/orders/stream?order_id={other.id}", headers=customer).status_code == 404
    filtered = client.get(f"/api/v1/orders/stream?order_id={order.id}&last_event_id=0", headers=auth)
    assert [row["order_id"] for row in frames(filtered)] == [str(order.id)]
    foreign = Tenant(name="Foreign", slug="foreign-stream")
    db.add(foreign)
    db.commit()
    _, foreign_auth = identity(db, tenant_id=foreign.id)
    assert frames(client.get("/api/v1/orders/stream?last_event_id=0", headers=foreign_auth)) == []
    assert client.get(f"/api/v1/orders/stream?order_id={order.id}", headers=foreign_auth).status_code == 404
    db.get(Tenant, order.tenant_id).status = "suspended"
    db.commit()
    assert client.get("/api/v1/orders/stream", headers=auth).status_code == 403
    assert client.get(f"/api/v1/public/track/{order.tracking_token}/stream").status_code == 404


def test_reader_reorders_delayed_ids_deduplicates_and_starts_now(db, workflow, stream_settings, monkeypatch):
    from app.services.sse import StreamScope, TimelineReader

    order, _ = workflow
    now = datetime.now(UTC)
    monkeypatch.setattr("app.services.sse.database_now", lambda db: now)
    reader = TimelineReader(TestingSessionLocal, StreamScope(order.tenant_id), stream_settings, cursor=0)
    timeline(db, order, "shipped", id=102, created_at=now)
    assert reader.poll() == []
    timeline(db, order, "paid", id=101, created_at=now)
    now += timedelta(seconds=1)
    assert [event.id for event in reader.poll()] == [101, 102]
    assert reader.poll() == []
    fresh = TimelineReader(TestingSessionLocal, StreamScope(order.tenant_id), stream_settings)
    assert fresh.poll() == []
    timeline(db, order, "delivered", id=103, created_at=now - timedelta(seconds=1))
    assert [event.id for event in fresh.poll()] == [103]
    db.get(Tenant, order.tenant_id).status = "suspended"
    db.commit()
    assert fresh.poll() is None


def test_memory_connection_caps_are_atomic_expire_and_release():
    from app.core.ratelimit import MemoryRateLimiter

    now = [0.0]
    limiter = MemoryRateLimiter(lambda: now[0])
    keys = (("tenant", 1), ("ip", 2))
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(lambda n: limiter.acquire(keys, str(n), 5), range(8)))
    assert sum(result) == 1
    winner = str(result.index(True))
    assert not limiter.acquire((("other-tenant", 2), ("ip", 1)), "blocked", 5)
    limiter.release(keys, winner)
    assert limiter.acquire((("other-tenant", 2), ("ip", 1)), "new", 5)
    now[0] = 6
    assert limiter.acquire(keys, "after-expiry", 5)
    limiter.release(keys, "after-expiry")


def test_reader_does_not_overtake_unread_backlog_with_buffered_high_id(db, workflow, stream_settings, monkeypatch):
    from app.services.sse import StreamScope, TimelineReader

    order, _ = workflow
    now = datetime.now(UTC)
    monkeypatch.setattr("app.services.sse.database_now", lambda db: now)
    reader = TimelineReader(TestingSessionLocal, StreamScope(order.tenant_id), stream_settings, cursor=0)
    timeline(db, order, id=100, created_at=now)
    assert reader.poll() == []
    for event_id in (1, 2, 3):
        timeline(db, order, id=event_id, created_at=now - timedelta(seconds=1))
    now += timedelta(seconds=1)
    assert [row.id for row in reader.poll()] == [1, 2]
    assert [row.id for row in reader.poll()] == [3, 100]


@pytest.mark.asyncio
async def test_stream_disconnect_releases_lease_and_closes_reader_sessions(db, workflow, stream_settings, monkeypatch):
    from app.services.sse import StreamResponse, StreamScope, TimelineReader

    order, _ = workflow
    reader = TimelineReader(TestingSessionLocal, StreamScope(order.tenant_id), stream_settings)
    polled = []
    original = reader.poll

    def poll():
        rows = original()
        polled.append(True)
        return rows

    monkeypatch.setattr(reader, "poll", poll)
    released = []

    class Lease:
        def release(self):
            released.append(True)

    ready = asyncio.Event()

    async def receive():
        await ready.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            assert TestingSessionLocal.kw["bind"].pool.checkedout() == 0
            if b"heartbeat" in message.get("body", b""):
                ready.set()

    response = StreamResponse(reader, Lease())
    await response({"type": "http"}, receive, send)
    assert released == [True] and polled


def test_stream_caps_outage_api_keys_and_lease_reuse(client, db, workflow, stream_settings, monkeypatch):
    from app.auth.principal import Principal
    from app.core.ratelimit import UnavailableRateLimiter
    from app.schemas.tenancy import ApiKeyCreate
    from app.services.sse import acquire_stream
    from app.services.tenancy import create_api_key

    order, _ = workflow
    timeline(db, order)
    owner, auth = identity(db)
    _, key = create_api_key(
        db, Principal(order.tenant_id, user=owner), ApiKeyCreate(name="read", scopes=["orders:read"])
    )
    assert len(frames(client.get("/api/v1/orders/stream?last_event_id=0", headers={"X-API-Key": key}))) == 1
    _, write = create_api_key(
        db, Principal(order.tenant_id, user=owner), ApiKeyCreate(name="write", scopes=["orders:write"])
    )
    assert client.get("/api/v1/orders/stream", headers={"X-API-Key": write}).status_code == 403
    stream_settings.sse_tenant_connection_limit = 1
    lease = acquire_stream(order.tenant_id, "testclient", stream_settings)
    path = f"/api/v1/public/track/{order.tracking_token}/stream"
    limited = client.get(path)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    assert limited.headers["cache-control"] == "no-store" and limited.headers["referrer-policy"] == "no-referrer"
    assert client.get("/api/v1/orders/stream", headers=auth).status_code == 429
    lease.release()
    assert frames(client.get(path)) == []
    stream_settings.sse_ip_connection_limit = 1
    lease = acquire_stream(uuid4(), "testclient", stream_settings)
    assert client.get(path).status_code == 429
    lease.release()
    monkeypatch.setattr("app.services.sse.get_limiter", lambda *args: UnavailableRateLimiter())
    failed = client.get(path)
    assert failed.status_code == 503 and failed.headers["cache-control"] == "no-store"


def test_reader_active_credential_rotation_budget_and_backlog_order(client, db, workflow, stream_settings):
    from app.auth.principal import Principal
    from app.core.ratelimit import RateLimitError
    from app.models.entities import ApiKey
    from app.models.enums import UserRole
    from app.schemas.tenancy import ApiKeyCreate
    from app.services.sse import StreamScope, TimelineReader
    from app.services.tenancy import create_api_key

    order, _ = workflow
    owner, _ = identity(db)
    scope = StreamScope(order.tenant_id, user_id=owner.id)
    reader = TimelineReader(TestingSessionLocal, scope, stream_settings, cursor=0)
    owner.role = UserRole.CUSTOMER
    db.commit()
    assert reader.poll() is None
    owner.role = UserRole.ADMIN
    db.commit()
    key, _ = create_api_key(
        db, Principal(order.tenant_id, user=owner), ApiKeyCreate(name="stream", scopes=["orders:read"])
    )
    keyed = TimelineReader(TestingSessionLocal, StreamScope(order.tenant_id, key_id=key.id), stream_settings)
    db.get(ApiKey, key.id).revoked_at = datetime.now(UTC)
    db.commit()
    assert keyed.poll() is None
    public = TimelineReader(
        TestingSessionLocal, StreamScope(order.tenant_id, order.id, tracking_version=1), stream_settings
    )
    order.tracking_version += 1
    db.commit()
    assert public.poll() is None
    # A full initial lookback must fail closed, never replay unseen history due to truncation.
    stream_settings.sse_max_events = 1
    stream_settings.sse_lookback_seconds = 60
    timeline(db, order, created_at=datetime.now(UTC))
    timeline(db, order, created_at=datetime.now(UTC))
    with pytest.raises(RateLimitError):
        TimelineReader(TestingSessionLocal, StreamScope(order.tenant_id), stream_settings)
    response = client.get(f"/api/v1/public/track/{order.tracking_token}/stream")
    assert response.status_code == 503 and response.headers["cache-control"] == "no-store"


def test_stream_event_budget_closes_with_resumable_cursor(client, db, workflow, stream_settings):
    order, _ = workflow
    _, auth = identity(db)
    rows = [timeline(db, order) for _ in range(3)]
    stream_settings.sse_max_events = 2
    response = client.get("/api/v1/orders/stream?last_event_id=0", headers=auth)
    assert len(frames(response)) == 2 and f"id: {rows[1].id}\n" in response.text
    response = client.get("/api/v1/orders/stream", headers={**auth, "Last-Event-ID": str(rows[1].id)})
    assert len(frames(response)) == 1 and f"id: {rows[2].id}\n" in response.text


def test_connection_backend_failures_are_safe_and_release_relies_on_lease(caplog):
    from redis.exceptions import RedisError

    from app.core.ratelimit import BackendUnavailable, RedisRateLimiter, UnavailableRateLimiter
    from app.services.sse import StreamLease

    class Broken:
        def eval(self, *args):
            raise RedisError("PRIVATE_BACKEND_DETAILS")

    backend = RedisRateLimiter(Broken())
    keys = (("tenant", 1),)
    with pytest.raises(BackendUnavailable):
        backend.acquire(keys, "opaque", 1)
    with pytest.raises(BackendUnavailable):
        backend.release(keys, "opaque")
    StreamLease(UnavailableRateLimiter(), keys, "opaque").release()
    assert "PRIVATE_BACKEND_DETAILS" not in caplog.text


@pytest.mark.postgres
def test_postgres_commit_order_gap_is_buffered_sorted_and_deduplicated(db, workflow, stream_settings, monkeypatch):
    from threading import Event

    from app.services.sse import StreamScope, TimelineReader

    order, _ = workflow
    tenant_id, order_id = order.tenant_id, order.id
    now = datetime.now(UTC)
    monkeypatch.setattr("app.services.sse.database_now", lambda db: now)
    reader = TimelineReader(TestingSessionLocal, StreamScope(tenant_id), stream_settings, cursor=0)
    ready, release = Event(), Event()

    def low_transaction():
        with TestingSessionLocal() as session:
            row = OrderTimeline(tenant_id=tenant_id, order_id=order_id, to_status="paid", created_at=now)
            session.add(row)
            session.flush()
            identity = row.id
            ready.set()
            assert release.wait(15)
            session.commit()
            return identity

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(low_transaction)
        try:
            assert ready.wait(15)
            with TestingSessionLocal.begin() as session:
                high = OrderTimeline(tenant_id=tenant_id, order_id=order_id, to_status="shipped", created_at=now)
                session.add(high)
                session.flush()
                high_id = high.id
            assert reader.poll() == []
        finally:
            release.set()
        low_id = future.result(timeout=15)
    now += timedelta(seconds=1)
    assert [row.id for row in reader.poll()] == [low_id, high_id]
    assert reader.poll() == [] and low_id < high_id
    assert TestingSessionLocal.kw["bind"].pool.checkedout() == 0
    print("SSE PG GAP: LOWER ID COMMITS LAST; SORTED ONCE; NO SESSION HELD BETWEEN POLLS")


@pytest.mark.redis
def test_real_redis_stream_caps_across_instances_and_expired_lease():
    import os

    from redis import Redis

    from app.core.ratelimit import RedisRateLimiter

    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Requires TEST_REDIS_URL")
    client = Redis.from_url(url, socket_timeout=2)
    namespace = "oos:test-stream:" + uuid4().hex + ":"
    peers = [RedisRateLimiter(client, namespace), RedisRateLimiter(client, namespace)]
    keys = (("tenant", 1), ("ip", 2))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda n: peers[n].acquire(keys, str(n), 10), range(2)))
        assert sum(results) == 1
        assert 0 < client.pttl(namespace + "stream:tenant") <= 11000
        peers[0].release(keys, str(results.index(True)))
        assert peers[1].acquire(keys, "after-release", 10)
        client.zadd(namespace + "stream:tenant", {"after-release": 0})
        client.zadd(namespace + "stream:ip", {"after-release": 0})
        assert peers[0].acquire(keys, "after-expiry", 10)
    finally:
        client.delete(*(namespace + "stream:" + key for key, _ in keys))
        client.close()
