from collections.abc import Callable
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.core.exceptions import AuthorizationError
from app.db.session import get_db
from app.models.entities import User
from app.models.enums import UserRole
from app.repositories.users import UserRepository

bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    try:
        payload = decode_access_token(token)
        user_id = UUID(payload["sub"])
    except Exception as exc:
        raise AuthorizationError("Invalid or expired token") from exc
    user = UserRepository(db).get(user_id)
    if not user or not user.is_active:
        raise AuthorizationError("Inactive or unknown user")
    return user


def require_roles(*roles: UserRole) -> Callable:
    def dependency(user: User = Depends(get_current_user)) -> User:
        if roles and user.role not in roles:
            raise AuthorizationError("Insufficient role privileges", {"required_roles": [r.value for r in roles]})
        return user

    return dependency