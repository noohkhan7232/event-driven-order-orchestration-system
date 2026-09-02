from sqlalchemy import select

from app.models.entities import User
from app.repositories.base import Repository


class UserRepository(Repository[User]):
    model = User

    def get_by_email(self, email: str) -> User | None:
        return self.db.scalar(select(User).where(User.email == email))
