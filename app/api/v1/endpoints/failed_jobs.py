from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.endpoints.admin import platform_admin
from app.auth.principal import Principal, get_principal
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.entities import FailedJob, User
from app.models.enums import JobStatus
from app.schemas.common import Page
from app.schemas.failed_jobs import FailedJobRead, ReplayAccepted
from app.services.failed_jobs import RecoveryConflict, discard_failed_job, replay_failed_job

router = APIRouter()
admin_router = APIRouter()


def owner(principal: Principal = Depends(get_principal)) -> User:
    return principal.require_owner()


def listing(
    db: Session, tenant_id: UUID | None, status: JobStatus | None, task: str | None, limit: int, offset: int
) -> Page[FailedJobRead]:
    conditions = []
    if tenant_id is not None:
        conditions.append(FailedJob.tenant_id == tenant_id)
    if status is not None:
        conditions.append(FailedJob.status == status)
    if task is not None:
        conditions.append(FailedJob.task_name == task)
    total = db.scalar(select(func.count()).select_from(FailedJob).where(*conditions)) or 0
    rows = db.scalars(
        select(FailedJob)
        .where(*conditions)
        .order_by(FailedJob.created_at.desc(), FailedJob.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return Page(items=[FailedJobRead.from_job(row) for row in rows], total=total, limit=limit, offset=offset)


def replay(db: Session, job_id: UUID, actor: User, tenant_id: UUID | None) -> ReplayAccepted:
    try:
        event = replay_failed_job(db, job_id, actor=actor.id, tenant_id=tenant_id)
    except ValidationError:
        # A poison legacy payload must not enter the unhandled-error logger.
        raise RecoveryConflict("order_not_retryable") from None
    return ReplayAccepted(id=job_id, event_id=event.event_id)


@router.get("", response_model=Page[FailedJobRead])
def list_owned(
    status: JobStatus | None = None,
    task: str | None = Query(None, max_length=160),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    actor: User = Depends(owner),
    db: Session = Depends(get_db),
):
    return listing(db, actor.tenant_id, status, task, limit, offset)


@router.get("/{job_id}", response_model=FailedJobRead)
def detail(job_id: UUID, actor: User = Depends(owner), db: Session = Depends(get_db)):
    row = db.scalar(select(FailedJob).where(FailedJob.id == job_id, FailedJob.tenant_id == actor.tenant_id))
    if row is None:
        raise NotFoundError("Failed job not found")
    return FailedJobRead.from_job(row)


@router.post("/{job_id}/replay", status_code=202, response_model=ReplayAccepted)
def replay_owned(job_id: UUID, actor: User = Depends(owner), db: Session = Depends(get_db)):
    return replay(db, job_id, actor, actor.tenant_id)


@router.post("/{job_id}/discard", status_code=204)
def discard_owned(job_id: UUID, actor: User = Depends(owner), db: Session = Depends(get_db)):
    discard_failed_job(db, job_id, actor=actor.id, tenant_id=actor.tenant_id)
    return Response(status_code=204)


@admin_router.get("", response_model=Page[FailedJobRead])
def list_all(
    tenant_id: UUID | None = None,
    status: JobStatus | None = None,
    task: str | None = Query(None, max_length=160),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    actor: User = Depends(platform_admin),
    db: Session = Depends(get_db),
):
    return listing(db, tenant_id, status, task, limit, offset)


@admin_router.post("/{job_id}/replay", status_code=202, response_model=ReplayAccepted)
def replay_any(job_id: UUID, actor: User = Depends(platform_admin), db: Session = Depends(get_db)):
    return replay(db, job_id, actor, None)


@admin_router.post("/{job_id}/discard", status_code=204)
def discard_any(job_id: UUID, actor: User = Depends(platform_admin), db: Session = Depends(get_db)):
    discard_failed_job(db, job_id, actor=actor.id)
    return Response(status_code=204)
