from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class ApiResponse(BaseModel, Generic[T]):
    data: T
    request_id: str | None = None


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict | None = None
