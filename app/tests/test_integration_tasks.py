import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from conftest import TestingSessionLocal

from app.core.config import Settings
from app.workers import tasks
from app.workers.celery_app import celery_app


def test_sqlite_local_guard_is_exclusive_and_releases_after_failure():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.integration_guard import integration_run

    engine = create_engine("sqlite://")
    factory = sessionmaker(engine)
    settings = Settings(_env_file=None, redis_url="")
    name = "local-proof-" + uuid4().hex
    try:
        with pytest.raises(ValueError):
            with integration_run(factory, settings, name) as acquired:
                assert acquired
                with integration_run(factory, settings, name) as overlap:
                    assert not overlap
                raise ValueError("fixture")
        with integration_run(factory, settings, name) as after:
            assert after
    finally:
        engine.dispose()


def test_redis_admission_failure_is_closed_and_release_failure_does_not_mask_work(monkeypatch):
    from redis.exceptions import RedisError

    from app.core import integration_guard

    closed = []

    class Client:
        def __init__(self, mode):
            self.mode = mode

        def set(self, *args, **kwargs):
            if self.mode == "unavailable":
                raise RedisError("private diagnostic")
            return self.mode != "overlap"

        def eval(self, *args):
            if self.mode == "release-failed":
                raise RedisError("private diagnostic")
            return 1

        def close(self):
            closed.append(self.mode)

    for mode in ("unavailable", "overlap", "release-failed", "ok"):
        monkeypatch.setattr(integration_guard.Redis, "from_url", lambda *args, mode=mode, **kwargs: Client(mode))
        with integration_guard.integration_run(TestingSessionLocal, Settings(_env_file=None), "fixture") as acquired:
            assert acquired == (mode in {"release-failed", "ok"})
    assert closed == ["unavailable", "overlap", "release-failed", "ok"]


def test_advisory_unlock_failure_discards_the_session(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from sqlalchemy.exc import SQLAlchemyError

    from app.core.integration_guard import integration_run

    connection = MagicMock()
    connection.execution_options.return_value = connection
    connection.__enter__.return_value = connection
    result = MagicMock()
    result.scalar.return_value = True
    connection.execute.side_effect = [result, SQLAlchemyError("fixture")]
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    engine.connect.return_value = connection
    factory = SimpleNamespace(kw={"bind": engine})
    with integration_run(factory, Settings(_env_file=None, redis_url=""), "fixture") as acquired:
        assert acquired
    connection.invalidate.assert_called_once()


def test_integrations_have_dedicated_queue_limits_and_compose_worker():
    services = yaml.safe_load(Path("docker-compose.yml").read_text())["services"]
    assert "integrations" not in services["worker"]["command"]
    worker = services["integrations-worker"]
    assert "--queues=integrations" in worker["command"] and "--concurrency=" in worker["command"]
    assert worker["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert "integrations" in {queue.name for queue in celery_app.conf.task_queues}
    for name in ("deliver_webhooks", "send_notifications"):
        task = getattr(tasks, name)
        assert celery_app.conf.task_routes[task.name]["queue"] == "integrations"
        assert 45 + 15 < task.soft_time_limit < task.time_limit
        assert celery_app.conf.beat_schedule[name.replace("_", "-")]["options"]["queue"] == "integrations"


@pytest.mark.parametrize(
    "name, runner", [("deliver_webhooks", "run_delivery_batch"), ("send_notifications", "run_notifications")]
)
def test_overlapping_task_returns_without_claiming_and_releases_on_exception(monkeypatch, name, runner):
    entered, release = threading.Event(), threading.Event()
    calls = []
    monkeypatch.setattr(tasks, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(tasks, "get_settings", lambda: Settings(_env_file=None, redis_url=""))

    def batch(*args):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return {"sent": 1}

    monkeypatch.setattr(tasks, runner, batch)
    task = getattr(tasks, name)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(task.run)
        try:
            assert entered.wait(3)
            started = time.monotonic()
            assert task.run() == {} and time.monotonic() - started < 0.5
            assert len(calls) == 1
        finally:
            release.set()
        assert running.result(timeout=5) == {"sent": 1}
    monkeypatch.setattr(tasks, runner, lambda *args: (_ for _ in ()).throw(ValueError("fixture")))
    with pytest.raises(ValueError):
        task.run()
    monkeypatch.setattr(tasks, runner, lambda *args: {"sent": 1})
    assert task.run() == {"sent": 1}


@pytest.mark.redis
def test_redis_guard_has_ttl_owner_safe_release_and_independent_task_keys():
    from redis import Redis

    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("Requires TEST_REDIS_URL")
    from app.core.integration_guard import integration_run

    settings = Settings(_env_file=None, redis_url=url)
    name = "proof-" + uuid4().hex
    client = Redis.from_url(url)
    key = "oos:integrations:" + name
    try:
        with integration_run(TestingSessionLocal, settings, name) as acquired:
            assert acquired and 90 < client.ttl(key) <= 105
            with integration_run(TestingSessionLocal, settings, name) as overlap:
                assert not overlap
            with integration_run(TestingSessionLocal, settings, name + "-other") as independent:
                assert independent
            client.set(key, "new-owner", ex=105)
        assert client.get(key) == b"new-owner"
        client.delete(key)
        with integration_run(TestingSessionLocal, settings, name) as again:
            assert again
        assert client.get(key) is None
    finally:
        client.delete(key)
        client.close()


@pytest.mark.postgres
def test_postgres_advisory_guard_is_nonblocking_and_holds_no_transaction():
    from sqlalchemy import text

    from app.core.integration_guard import integration_run

    settings = Settings(_env_file=None, redis_url="")
    name = "proof-" + uuid4().hex
    with integration_run(TestingSessionLocal, settings, name) as acquired:
        assert acquired
        with ThreadPoolExecutor(max_workers=1) as pool:

            def overlap():
                with integration_run(TestingSessionLocal, settings, name) as other:
                    return other

            assert pool.submit(overlap).result(timeout=2) is False
        with TestingSessionLocal() as db:
            assert (
                db.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                        "AND pid<>pg_backend_pid() AND state='idle in transaction'"
                    )
                )
                == 0
            )
    with integration_run(TestingSessionLocal, settings, name) as after:
        assert after
