from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from conftest import TestingSessionLocal
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_payment_config import identity

from app.auth.principal import Principal
from app.auth.security import create_access_token
from app.db.session import get_db
from app.events.contracts import DomainEvent, EventNames
from app.main import app
from app.models.entities import AuditLog, FailedJob, OutboxEvent, Tenant, User
from app.models.enums import JobStatus, OrderStatus, UserRole
from app.schemas.tenancy import ApiKeyCreate
from app.services.tenancy import create_api_key

PATH = "/api/v1/failed-jobs"
ADMIN = "/api/v1/admin/failed-jobs"


def job(db, order, **values):
    event = DomainEvent(
        event_type=EventNames.ORDER_CREATED,
        aggregate_id=order.id,
        tenant_id=order.tenant_id,
        payload={"order_id": str(order.id)},
    )
    row = FailedJob(
        tenant_id=order.tenant_id,
        aggregate_id=order.id,
        event_id=event.event_id,
        payload=event.model_dump(mode="json")
        | {"private_legacy_details": {"buyer_email": "private-buyer@example.com", "credential": "PRIVATE_CREDENTIAL"}},
        task_name=values.pop("task_name", "app.workers.tasks.process_event"),
        queue_name=values.pop("queue_name", "orders"),
        error_message=values.pop("error_message", "private-buyer@example.com PRIVATE_CREDENTIAL +919999999999"),
        **values,
    )
    db.add(row)
    db.commit()
    return row


def assert_private(response):
    for private in (
        "private-buyer",
        "PRIVATE_CREDENTIAL",
        "+919999999999",
        '"payload"',
        '"buyer_email"',
        '"credential"',
    ):
        assert private not in response.text


def test_owner_list_filters_pagination_detail_and_safe_error_projection(client, db, workflow):
    order, _ = workflow
    first = job(db, order)
    second = job(
        db,
        order,
        task_name="app.workers.tasks.process_refund",
        status=JobStatus.DISCARDED,
        error_message="RETRIES_EXHAUSTED",
    )
    _, auth = identity(db)
    response = client.get(PATH, headers=auth)
    assert response.status_code == 200 and response.json()["total"] == 2
    assert_private(response)
    assert len(client.get(PATH + "?limit=1&offset=1", headers=auth).json()["items"]) == 1
    filtered = client.get(PATH + "?status=discarded&task=app.workers.tasks.process_refund", headers=auth)
    assert [row["id"] for row in filtered.json()["items"]] == [str(second.id)]
    assert filtered.json()["items"][0]["error_message"] == "RETRIES_EXHAUSTED"
    detail = client.get(f"{PATH}/{first.id}", headers=auth)
    assert detail.status_code == 200 and detail.json()["id"] == str(first.id)
    assert len(detail.json()["error_message"]) <= 160
    assert_private(detail)
    assert client.get(PATH + "?limit=101", headers=auth).status_code == 422
    assert client.get(PATH + "?status=not-valid", headers=auth).status_code == 422
    assert client.get(f"{PATH}/{uuid4()}", headers=auth).status_code == 404
    assert client.get(PATH).status_code == 401


@pytest.mark.parametrize("role", [UserRole.VENDOR, UserRole.CUSTOMER, "key"])
def test_failed_jobs_require_owner_jwt_on_every_tenant_route(client, db, workflow, role):
    row = job(db, workflow[0])
    user, headers = identity(db, role=UserRole.ADMIN if role == "key" else role)
    if role == "key":
        _, key = create_api_key(
            db, Principal(user.tenant_id, user=user), ApiKeyCreate(name="integration", scopes=["orders:write"])
        )
        headers = {"X-API-Key": key}
    for method, path in (
        ("GET", PATH),
        ("GET", f"{PATH}/{row.id}"),
        ("POST", f"{PATH}/{row.id}/replay"),
        ("POST", f"{PATH}/{row.id}/discard"),
    ):
        assert client.request(method, path, headers=headers).status_code == 403
    assert client.get(ADMIN, headers=headers).status_code == 403


def test_foreign_jobs_are_hidden_and_platform_admin_can_filter_and_recover(client, db, workflow):
    order, _ = workflow
    row = job(db, order)
    foreign = Tenant(name="Foreign", slug="foreign-jobs")
    db.add(foreign)
    db.commit()
    _, auth = identity(db, tenant_id=foreign.id)
    assert client.get(PATH, headers=auth).json()["total"] == 0
    for method, path in (
        ("GET", f"{PATH}/{row.id}"),
        ("POST", f"{PATH}/{row.id}/replay"),
        ("POST", f"{PATH}/{row.id}/discard"),
    ):
        assert client.request(method, path, headers=auth).status_code == 404
    assert client.get(ADMIN, headers=auth).status_code == 403
    root = User(
        tenant_id=None,
        is_platform_admin=True,
        email=uuid4().hex + "@example.com",
        full_name="Platform",
        hashed_password="unused",
        role=UserRole.ADMIN,
    )
    db.add(root)
    db.commit()
    admin_auth = {"Authorization": "Bearer " + create_access_token(str(root.id))}
    response = client.get(ADMIN, headers=admin_auth)
    assert response.status_code == 200 and response.json()["total"] == 1
    assert_private(response)
    assert client.get(ADMIN + f"?tenant_id={foreign.id}", headers=admin_auth).json()["total"] == 0
    assert client.get(PATH, headers=admin_auth).status_code == 403
    replay = client.post(f"{ADMIN}/{row.id}/replay", headers=admin_auth)
    assert replay.status_code == 202
    assert_private(replay)
    second = job(db, order)
    assert client.post(f"{ADMIN}/{second.id}/discard", headers=admin_auth).status_code == 204
    assert db.scalar(select(AuditLog).where(AuditLog.entity_id == second.id)).actor_id == root.id


def test_replay_discard_once_and_actor_audits(client, db, workflow):
    order, _ = workflow
    first, second = job(db, order), job(db, order)
    owner, auth = identity(db)
    replay = client.post(f"{PATH}/{first.id}/replay", headers=auth)
    assert replay.status_code == 202 and replay.json() == {
        "id": str(first.id),
        "status": "replayed",
        "event_id": str(first.event_id),
    }
    assert_private(replay)
    again = client.post(f"{PATH}/{first.id}/replay", headers=auth)
    assert again.status_code == 409 and again.json()["code"] == "job_already_replayed"
    assert client.post(f"{PATH}/{second.id}/discard", headers=auth).status_code == 204
    refused = client.post(f"{PATH}/{second.id}/replay", headers=auth)
    assert refused.status_code == 409 and refused.json()["code"] == "order_not_retryable"
    db.expire_all()
    assert db.get(FailedJob, first.id).status == JobStatus.REPLAYED
    assert db.get(FailedJob, second.id).status == JobStatus.DISCARDED
    assert db.query(OutboxEvent).filter_by(event_id=first.event_id).count() == 1
    outbox = db.query(OutboxEvent).filter_by(event_id=first.event_id).one()
    assert "PRIVATE_CREDENTIAL" not in str(outbox.payload)
    audits = db.scalars(select(AuditLog).where(AuditLog.entity_type == "failed_job")).all()
    assert len(audits) == 2 and all(row.actor_id == owner.id for row in audits)


def test_business_failure_suspension_safe_labels_and_technical_reopen(client, db, workflow):
    order, _ = workflow
    row = job(db, order, task_name="PRIVATE_CREDENTIAL", queue_name="private-buyer@example.com")
    _, auth = identity(db)
    detail = client.get(f"{PATH}/{row.id}", headers=auth)
    assert_private(detail)
    assert detail.json()["task_name"] == detail.json()["queue_name"] == "unknown"
    order.status, order.failure_reason = OrderStatus.FAILED, "PAYMENT_DECLINED"
    db.commit()
    result = client.post(f"{PATH}/{row.id}/replay", headers=auth)
    assert result.status_code == 409 and result.json()["code"] == "order_not_retryable"
    order.failure_reason = "RETRIES_EXHAUSTED"
    db.commit()
    result = client.post(f"{PATH}/{row.id}/replay", headers=auth)
    assert result.status_code == 202 and result.json()["event_id"] != str(row.event_id)
    db.refresh(order)
    assert order.status == OrderStatus.CREATED
    db.get(Tenant, order.tenant_id).status = "suspended"
    db.commit()
    response = client.get(PATH, headers=auth)
    assert response.status_code == 403 and response.json()["code"] == "tenant_suspended"


def test_poison_payload_is_a_safe_conflict_without_logging_private_validation(client, db, workflow, caplog):
    row = job(db, workflow[0])
    row.payload = {"credential": "PRIVATE_CREDENTIAL", "buyer_email": "private-buyer@example.com"}
    db.commit()
    _, auth = identity(db)
    response = client.post(f"{PATH}/{row.id}/replay", headers=auth)
    assert response.status_code == 409 and response.json()["code"] == "order_not_retryable"
    assert_private(response)
    assert "PRIVATE_CREDENTIAL" not in caplog.text and "private-buyer" not in caplog.text
    assert db.query(OutboxEvent).count() == 0
    assert db.query(AuditLog).filter_by(entity_type="failed_job").count() == 0


@pytest.mark.postgres
def test_postgres_concurrent_http_replay_has_one_effect(db, workflow, monkeypatch):
    import app.api.v1.endpoints.failed_jobs as api

    row = job(db, workflow[0])
    owner, auth = identity(db)
    job_id, event_id, actor_id = row.id, row.event_id, owner.id
    barrier = Barrier(2)
    original = api.replay_failed_job

    def replay(*args, **kwargs):
        barrier.wait(timeout=15)
        return original(*args, **kwargs)

    def request_db():
        with TestingSessionLocal() as session:
            yield session

    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = request_db
    monkeypatch.setattr(api, "replay_failed_job", replay)

    def request(_):
        with TestClient(app) as client:
            return client.post(f"{PATH}/{job_id}/replay", headers=auth)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(request, range(2)))
    finally:
        if previous:
            app.dependency_overrides[get_db] = previous
        else:
            app.dependency_overrides.pop(get_db, None)
    assert sorted(response.status_code for response in responses) == [202, 409]
    assert (
        next(response for response in responses if response.status_code == 409).json()["code"] == "job_already_replayed"
    )
    assert db.query(OutboxEvent).filter_by(event_id=event_id).count() == 1
    audits = db.query(AuditLog).filter_by(entity_id=job_id, action="failed_job_replayed").all()
    assert len(audits) == 1 and audits[0].actor_id == actor_id
    print("FAILED JOB HTTP PG RACE: TWO REQUESTS, ONE 202/ONE 409, ONE OUTBOX EFFECT AND ACTOR AUDIT")
