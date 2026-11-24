import json
import re
from pathlib import Path

import pytest
from conftest import TestingSessionLocal
from test_failed_jobs_api import job
from test_payment_config import identity
from test_sse import stream_settings as stream_settings
from test_sse import timeline

from app.auth.principal import Principal
from app.auth.security import create_access_token
from app.models.entities import User
from app.models.enums import UserRole
from app.schemas.tenancy import ApiKeyCreate
from app.services.tenancy import create_api_key

ROOT = Path(__file__).resolve().parents[2]


def test_openapi_export_matches_current_contract():
    from scripts.export_openapi import render_openapi

    exported = (ROOT / "docs/openapi.json").read_text(encoding="utf-8")
    assert exported == render_openapi()
    schema = json.loads(exported)
    assert "text/event-stream" in schema["paths"]["/api/v1/orders/stream"]["get"]["responses"]["200"]["content"]
    assert "payload" not in schema["components"]["schemas"]["FailedJobRead"]["properties"]
    assert "PRIVATE_CREDENTIAL" not in exported


def test_openapi_check_detects_staleness_and_runs_from_another_cwd(tmp_path, monkeypatch):
    import scripts.export_openapi as exporter

    monkeypatch.chdir(tmp_path)
    target = tmp_path / "contract.json"
    monkeypatch.setattr(exporter, "TARGET", target)
    assert exporter.main(["--check"]) == 1
    assert exporter.main([]) == 0 and target.is_file()
    target.write_text("{}\n", encoding="utf-8")
    assert exporter.main(["--check"]) == 1 and target.read_text(encoding="utf-8") == "{}\n"
    assert exporter.main([]) == 0 and exporter.main(["--check"]) == 0


def test_example_environment_has_no_duplicate_assignments():
    names = re.findall(r"^([A-Z][A-Z0-9_]*)=", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M)
    assert len(names) == len(set(names)), [name for name in names if names.count(name) > 1]


def test_coverage_gate_enforces_overall_each_module_and_missing_evidence(tmp_path):
    from scripts.check_coverage import MODULES, main

    target = tmp_path / "coverage.json"
    document = {
        "totals": {"percent_covered": 90},
        "files": {name.replace("/", "\\"): {"summary": {"percent_covered": 90}} for name in MODULES},
    }

    def check():
        target.write_text(json.dumps(document), encoding="utf-8")
        return main([str(target)])

    assert check() == 0
    document["totals"]["percent_covered"] = 89.99
    assert check() == 1
    document["totals"]["percent_covered"] = 99
    selected = MODULES[0].replace("/", "\\")
    document["files"][selected]["summary"]["percent_covered"] = 89.99
    assert check() == 1
    del document["files"][selected]
    assert check() == 1


def test_public_stream_validation_keeps_privacy_headers(client, workflow, stream_settings):
    token = workflow[0].tracking_token
    response = client.get(f"/api/v1/public/track/{token}/stream?last_event_id=PRIVATE_CREDENTIAL")
    assert response.status_code == 422 and "PRIVATE_CREDENTIAL" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_new_routes_never_return_buyer_credentials_payloads_or_raw_errors(client, db, workflow, stream_settings):
    order, _ = workflow
    order.buyer_name, order.buyer_email, order.buyer_phone = (
        "PRIVATE_BUYER",
        "private-buyer@example.com",
        "+919999999999",
    )
    db.commit()
    timeline(db, order)
    owner, auth = identity(db)
    _, key = create_api_key(
        db, Principal(order.tenant_id, user=owner), ApiKeyCreate(name="read", scopes=["orders:read"])
    )
    rows = [job(db, order) for _ in range(4)]
    root = User(
        tenant_id=None,
        is_platform_admin=True,
        email="quality-root@example.com",
        full_name="Platform",
        hashed_password="unused",
        role=UserRole.ADMIN,
    )
    db.add(root)
    db.commit()
    admin = {"Authorization": "Bearer " + create_access_token(str(root.id))}
    calls = [
        ("GET", "/api/v1/orders/stream?last_event_id=0", {"X-API-Key": key}, 200),
        ("GET", f"/api/v1/public/track/{order.tracking_token}/stream?last_event_id=0", {}, 200),
        ("GET", "/api/v1/failed-jobs", auth, 200),
        ("GET", f"/api/v1/failed-jobs/{rows[0].id}", auth, 200),
        ("POST", f"/api/v1/failed-jobs/{rows[0].id}/replay", auth, 202),
        ("POST", f"/api/v1/failed-jobs/{rows[1].id}/discard", auth, 204),
        ("GET", "/api/v1/admin/failed-jobs", admin, 200),
        ("POST", f"/api/v1/admin/failed-jobs/{rows[2].id}/replay", admin, 202),
        ("POST", f"/api/v1/admin/failed-jobs/{rows[3].id}/discard", admin, 204),
        ("GET", "/api/v1/orders/stream", admin, 403),
    ]
    for method, path, headers, expected in calls:
        response = client.request(method, path, headers=headers)
        assert response.status_code == expected
        for private in (
            "PRIVATE_BUYER",
            "private-buyer",
            "+919999999999",
            "PRIVATE_CREDENTIAL",
            key,
            order.tracking_token,
            '"buyer_email"',
            '"payload"',
        ):
            assert private not in response.text


@pytest.mark.asyncio
async def test_active_public_stream_closes_after_token_rotation_without_session_leak(db, workflow, stream_settings):
    from app.services.sse import StreamScope, TimelineReader

    order, _ = workflow
    reader = TimelineReader(
        TestingSessionLocal,
        StreamScope(order.tenant_id, order.id, tracking_version=order.tracking_version),
        stream_settings,
    )
    events = reader.events()
    assert await anext(events) == ": connected\n\n"
    order.tracking_version += 1
    db.commit()
    with pytest.raises(StopAsyncIteration):
        await anext(events)
    assert TestingSessionLocal.kw["bind"].pool.checkedout() == 0


@pytest.mark.asyncio
async def test_stream_max_duration_also_bounds_a_blocked_client_send(stream_settings):
    import asyncio
    from types import SimpleNamespace

    from app.services.sse import StreamResponse

    stream_settings.sse_max_seconds = 0.03
    released, finished = [], []

    class Lease:
        def release(self):
            released.append(True)

    async def events():
        yield ": connected\n\n"

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.body":
            if message.get("more_body"):
                await asyncio.sleep(10)
            else:
                finished.append(True)

    response = StreamResponse(SimpleNamespace(settings=stream_settings, events=events), Lease())
    await asyncio.wait_for(response({"type": "http"}, receive, send), timeout=1)
    assert released == [True] and finished == [True]
