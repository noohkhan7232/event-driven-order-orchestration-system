import secrets

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth.principal import AmbiguousCredentials, get_principal
from app.core.config import Settings, get_settings
from app.core.exceptions import AuthorizationError
from app.db.session import SessionLocal, get_db
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


def require_platform_access(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> None:
    if authorization is not None and x_api_key is not None:
        raise AmbiguousCredentials("Supply exactly one credential")
    scheme, _, token = (authorization or "").partition(" ")
    if (
        settings.metrics_token
        and scheme.lower() == "bearer"
        and secrets.compare_digest(token.encode(), settings.metrics_token.encode())
    ):
        return
    principal = get_principal(authorization, x_api_key, db)
    if not principal.is_platform_admin:
        raise AuthorizationError("Platform administrator required")


@router.get("/metrics", dependencies=[Depends(require_platform_access)])
def metrics():
    return Response(metrics_response(), media_type="text/plain; version=0.0.4")


@router.get("/workers", dependencies=[Depends(require_platform_access)])
def workers():
    return {
        "queues": ["orders", "inventory", "payments", "shipments", "dead_letter"],
        "scheduled_jobs": ["low-stock-scan-every-5-minutes", "shipment-delay-scan-every-10-minutes"],
    }
