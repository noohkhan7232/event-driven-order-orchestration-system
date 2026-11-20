from collections.abc import Callable

from fastapi import Depends

from app.auth.principal import Principal, get_principal
from app.core.exceptions import AuthorizationError
from app.models.entities import User
from app.models.enums import UserRole


def get_current_user(principal: Principal = Depends(get_principal)) -> User:
    if principal.user is None:
        raise AuthorizationError("JWT credentials required")
    return principal.user


def require_roles(*roles: UserRole) -> Callable:
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise AuthorizationError("Insufficient role privileges")
        return user

    return dependency
