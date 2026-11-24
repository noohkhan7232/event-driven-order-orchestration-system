"""Per-task admission: Redis TTL or session advisory lock, never business row locks."""

import hashlib
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import INTEGRATION_LOCK_SECONDS, Settings

_local_locks: dict[str, Lock] = {}
_registry_lock = Lock()
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


@contextmanager
def integration_run(factory: sessionmaker[Session], settings: Settings, name: str) -> Iterator[bool]:
    key = "oos:integrations:" + name
    if settings.redis_url:
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2)
        token = secrets.token_hex(24)
        try:
            acquired = bool(client.set(key, token, nx=True, ex=INTEGRATION_LOCK_SECONDS))
        except RedisError:
            client.close()
            yield False
            return
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    client.eval(_RELEASE, 1, key, token)
                except RedisError:
                    # The hard task limit precedes TTL expiry. Do not delete a
                    # replacement owner's lock after a timeout or network loss.
                    pass
            client.close()
        return
    engine = factory.kw["bind"]
    if engine.dialect.name == "postgresql":
        lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            acquired = bool(connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id}).scalar())
            try:
                yield acquired
            finally:
                if acquired:
                    try:
                        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id})
                    except SQLAlchemyError:
                        connection.invalidate()
        return
    # SQLite is supported by local tests, not as distributed production admission.
    with _registry_lock:
        lock = _local_locks.setdefault(key, Lock())
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()
