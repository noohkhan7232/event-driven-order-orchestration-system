"""First-hit fixed windows; Redis updates count and TTL in one atomic script."""

import hashlib
import ipaddress
import math
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

from prometheus_client import Counter
from redis import Redis
from redis.exceptions import RedisError
from starlette.requests import Request

from app.core.config import Settings
from app.core.exceptions import DomainError
from app.core.logging import get_logger

logger = get_logger(__name__)
DEGRADED = Counter("rate_limit_degraded_total", "Unavailable rate-limit backend", ["policy", "mode"])


class BackendUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    reset: int
    retry_after: int

    def headers(self) -> dict[str, str]:
        result = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset),
        }
        if not self.allowed:
            result["Retry-After"] = str(self.retry_after)
        return result


class RateLimiter(ABC):
    @abstractmethod
    def hit(self, key: str, limit: int, window: int) -> Decision:
        raise NotImplementedError


def decision(count: int, limit: int, expires: float, now: float) -> Decision:
    return Decision(count <= limit, limit, max(0, limit - count), math.ceil(expires), max(1, math.ceil(expires - now)))


class MemoryRateLimiter(RateLimiter):
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.lock = Lock()
        self.windows: dict[str, tuple[int, float]] = {}

    def hit(self, key: str, limit: int, window: int) -> Decision:
        with self.lock:
            now = self.clock()
            if len(self.windows) > 1024:
                self.windows = {k: v for k, v in self.windows.items() if v[1] > now}
            count, expires = self.windows.get(key, (0, now + window))
            if expires <= now:
                count, expires = 0, now + window
            count += 1
            self.windows[key] = count, expires
            return decision(count, limit, expires, now)


SCRIPT = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('PTTL', KEYS[1])
if count == 1 or ttl < 0 then
    redis.call('PEXPIRE', KEYS[1], ARGV[1])
    ttl = tonumber(ARGV[1])
end
local now = redis.call('TIME')
return {count, ttl, now[1], now[2]}
"""


class RedisRateLimiter(RateLimiter):
    def __init__(self, client: Redis, namespace: str = "oos:rate:") -> None:
        self.client = client
        self.namespace = namespace

    def hit(self, key: str, limit: int, window: int) -> Decision:
        try:
            response = self.client.eval(SCRIPT, 1, self.namespace + key, str(window * 1000))
            if not isinstance(response, list) or len(response) != 4:
                raise BackendUnavailable()
            count, ttl, seconds, micros = response
        except RedisError:
            raise BackendUnavailable() from None
        now = int(seconds) + int(micros) / 1000000
        return decision(int(count), limit, now + int(ttl) / 1000, now)


class UnavailableRateLimiter(RateLimiter):
    def hit(self, key: str, limit: int, window: int) -> Decision:
        raise BackendUnavailable()


def build_limiter(url: str | None, local: bool) -> RateLimiter:
    if url:
        try:
            return RedisRateLimiter(Redis.from_url(url, socket_connect_timeout=0.2, socket_timeout=0.2))
        except (ValueError, RedisError):
            return UnavailableRateLimiter()
    return MemoryRateLimiter() if local else UnavailableRateLimiter()


class LimiterFactory:
    def __init__(self) -> None:
        self.lock = Lock()
        self.backends: dict[tuple[str | None, bool], RateLimiter] = {}

    def __call__(self, url: str | None, local: bool) -> RateLimiter:
        # lru_cache alone may run a constructor twice on concurrent cache misses.
        with self.lock:
            key = url, local
            if key not in self.backends:
                self.backends[key] = build_limiter(url, local)
            return self.backends[key]

    def cache_clear(self) -> None:
        with self.lock:
            for backend in self.backends.values():
                if isinstance(backend, RedisRateLimiter):
                    backend.client.close()
            self.backends.clear()


get_limiter = LimiterFactory()


@dataclass(frozen=True)
class Policy:
    name: str
    limit: int
    window: int
    key_function: str
    fail_open: bool = False


def authenticated_policy(settings: Settings) -> Policy:
    return Policy(
        "authenticated",
        settings.rate_limit_authenticated_limit,
        settings.rate_limit_authenticated_window_seconds,
        "principal",
        settings.rate_limit_fail_open,
    )


def policies_for(method: str, path: str, settings: Settings) -> tuple[Policy, ...]:
    registry = {
        ("POST", "/api/v1/auth/login"): (
            Policy("login_ip", settings.rate_limit_login_ip_limit, settings.rate_limit_login_ip_window_seconds, "ip"),
            Policy(
                "login_email",
                settings.rate_limit_login_email_limit,
                settings.rate_limit_login_email_window_seconds,
                "email_ip",
            ),
        ),
        ("POST", "/api/v1/auth/signup"): (
            Policy("signup", settings.rate_limit_signup_limit, settings.rate_limit_signup_window_seconds, "ip"),
        ),
        ("POST", "/api/v1/tenants/register"): (
            Policy("register", settings.rate_limit_register_limit, settings.rate_limit_register_window_seconds, "ip"),
        ),
        ("POST", "/api/v1/webhooks/payments/{provider}/{webhook_public_id}"): (
            Policy("webhook_ip", settings.rate_limit_webhook_limit, settings.rate_limit_webhook_window_seconds, "ip"),
            Policy(
                "webhook_id",
                settings.rate_limit_webhook_limit,
                settings.rate_limit_webhook_window_seconds,
                "webhook_id",
            ),
        ),
        ("GET", "/api/v1/public/track/{token}"): (
            Policy("tracking", settings.rate_limit_tracking_limit, settings.rate_limit_tracking_window_seconds, "ip"),
        ),
    }
    for public_path in [
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/api/v1/openapi.json",
        "/api/v1/system/health",
        "/api/v1/system/ready",
    ]:
        for public_method in ["GET", "HEAD"]:
            registry[public_method, public_path] = (
                Policy(
                    "public", settings.rate_limit_public_limit, settings.rate_limit_public_window_seconds, "ip", True
                ),
            )
    return registry.get((method, path), ())


def client_ip(request: Request, trusted_count: int) -> str:
    peer = request.client.host if request.client else "unknown"
    if trusted_count:
        entries = [value.strip() for value in request.headers.get("x-forwarded-for", "").split(",")]
        if len(entries) >= trusted_count:
            try:
                return str(ipaddress.ip_address(entries[-trusted_count]))
            except ValueError:
                pass
    return peer


class RateLimitError(DomainError):
    def __init__(self, *, result: Decision | None = None) -> None:
        self.status_code = 429 if result else 503
        self.code = "rate_limited" if result else "rate_limit_unavailable"
        self.headers = result.headers() if result else {"Retry-After": "1"}
        super().__init__("Too many requests" if result else "Request protection temporarily unavailable")


def enforce(request: Request, settings: Settings, policy: Policy, identity: str) -> None:
    # Store only digests of key material (including email/IP); never credentials.
    key = hashlib.sha256(f"{policy.name}:{policy.window}:{identity}".encode()).hexdigest()
    try:
        result = get_limiter(
            settings.rate_limit_redis_url, settings.environment in {"local", "development", "test"}
        ).hit(key, policy.limit, policy.window)
    except BackendUnavailable:
        mode = "open" if policy.fail_open else "closed"
        DEGRADED.labels(policy.name, mode).inc()
        logger.warning("rate_limit_degraded", policy=policy.name, mode=mode)
        if policy.fail_open:
            return
        raise RateLimitError() from None
    request.state.rate_limit_headers = result.headers()
    if not result.allowed:
        raise RateLimitError(result=result)
