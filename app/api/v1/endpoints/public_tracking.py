from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.services.tracking import PUBLIC_HEADERS, public_projection, tracked_order

router = APIRouter()


@router.get("/{token}")
def track(token: str, response: Response, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    for name, value in PUBLIC_HEADERS.items():
        response.headers[name] = value
    order, tenant = tracked_order(db, token, settings)
    return public_projection(db, order, tenant, settings)
