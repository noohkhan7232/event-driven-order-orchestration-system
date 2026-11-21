from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.core.exceptions import DomainError
from app.db.session import get_db
from app.services.inbound_payments import accept_webhook

router = APIRouter()


class WebhookTooLarge(DomainError):
    status_code = 413
    code = "payload_too_large"


@router.post("/{provider}/{webhook_public_id}")
async def receive(
    provider: str,
    webhook_public_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, bool]:
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > 65536:
            raise WebhookTooLarge("Request body too large")
        chunks.extend(chunk)
    await run_in_threadpool(
        accept_webhook,
        db,
        settings,
        provider,
        webhook_public_id,
        bytes(chunks),
        dict(request.headers),
        getattr(request.state, "request_id", None),
    )
    return {"accepted": True}
