from fastapi import APIRouter, Response
from sqlalchemy import text

from app.db.session import SessionLocal
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
def metrics():
    return Response(metrics_response(), media_type="text/plain; version=0.0.4")


@router.get("/workers")
def workers():
    return {
        "queues": ["orders", "inventory", "payments", "shipments", "dead_letter"],
        "scheduled_jobs": ["low-stock-scan-every-5-minutes", "shipment-delay-scan-every-10-minutes"],
    }
