from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

ModelT = TypeVar("ModelT")


class Repository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, db: Session):
        self.db = db

    def get(self, entity_id: UUID) -> ModelT | None:
        return self.db.get(self.model, entity_id)

    def list(self, limit: int = 50, offset: int = 0) -> tuple[list[ModelT], int]:
        items = self.db.scalars(select(self.model).limit(limit).offset(offset)).all()
        total = self.db.scalar(select(func.count()).select_from(self.model)) or 0
        return list(items), total

    def add(self, entity: ModelT) -> ModelT:
        self.db.add(entity)
        self.db.flush()
        return entity
