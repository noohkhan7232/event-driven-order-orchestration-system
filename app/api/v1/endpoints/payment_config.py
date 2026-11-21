from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.auth.principal import Principal, get_principal
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.schemas.payment_config import PaymentConfigRead, PaymentConfigWrite
from app.services.payment_config import masked_config, owned_config, save_config

router = APIRouter()


@router.put("/razorpay", response_model=PaymentConfigRead)
def put_config(
    payload: PaymentConfigWrite,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PaymentConfigRead:
    return masked_config(save_config(db, principal, payload, settings), settings)


@router.get("/razorpay", response_model=PaymentConfigRead)
def get_config(
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PaymentConfigRead:
    return masked_config(owned_config(db, principal), settings)


@router.delete("/razorpay", status_code=204)
def delete_config(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> Response:
    try:
        row = owned_config(db, principal, lock=True)
        row.is_active = False
        db.commit()
    except Exception:
        db.rollback()
        raise
    return Response(status_code=204)
