from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.models.entities import User
from app.schemas.auth import LoginRequest, Token, UserCreate, UserRead
from app.services.auth import AuthService

router = APIRouter()


@router.post("/signup", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def signup(payload: UserCreate, db: Session = Depends(get_db)):
    return AuthService(db).register(payload)


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    return Token(access_token=AuthService(db).login(payload.email, payload.password))


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)):
    return user
