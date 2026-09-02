from sqlalchemy.orm import Session

from app.auth.security import create_access_token, hash_password, verify_password
from app.core.exceptions import ConflictError, AuthorizationError
from app.models.entities import User
from app.repositories.users import UserRepository
from app.schemas.auth import UserCreate


class AuthService:
    def __init__(self, db: Session):
        self.db = db
        self.users = UserRepository(db)

    def register(self, payload: UserCreate) -> User:
        if self.users.get_by_email(payload.email):
            raise ConflictError("A user with this email already exists")
        user = User(
            email=payload.email.lower(),
            hashed_password=hash_password(payload.password),
            full_name=payload.full_name,
            role=payload.role,
        )
        self.users.add(user)
        self.db.commit()
        return user

    def login(self, email: str, password: str) -> str:
        user = self.users.get_by_email(email.lower())
        if not user or not verify_password(password, user.hashed_password):
            raise AuthorizationError("Invalid email or password")
        return create_access_token(str(user.id), {"role": user.role.value})
