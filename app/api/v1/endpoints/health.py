import secrets

from fastapi import APIRouter, Depends, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.core.config import Settings, get_settings
from app.core.exceptions import AuthorizationError, NotFoundError
from app.db.session import SessionLocal, get_db
from app.models.enums import UserRole
from app.observability.metrics import metrics_response

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "service": "event-driven-order-orchestration-system"}


@router.get("/ready")
def readiness():
    with SessionLocal() as db:
        db.execute(text("select 1"))
    return {"status": "ready", "database": "ok"}


@router.get("/metrics")
def metrics(
    settings: Settings = Depends(get_settings),
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
    db: Session = Depends(get_db),
):
    if settings.environment not in {"local", "development", "test"}:
        if not settings.metrics_token:
            raise NotFoundError("Metrics disabled")
        if credentials is None:
            raise AuthorizationError("Metrics credentials required")
        # Bytes support arbitrary Unicode tokens while retaining constant-time comparison.
        if not secrets.compare_digest(credentials.credentials.encode(), settings.metrics_token.encode()):
            user = get_current_user(credentials, db)
            if user.role != UserRole.ADMIN:
                raise AuthorizationError("Administrator role required")
    return Response(metrics_response(), media_type="text/plain; version=0.0.4")


@router.get("/workers")
def workers():
    return {
        "queues": ["orders", "inventory", "payments", "shipments", "dead_letter"],
        "scheduled_jobs": ["low-stock-scan-every-5-minutes", "shipment-delay-scan-every-10-minutes"],
    }
