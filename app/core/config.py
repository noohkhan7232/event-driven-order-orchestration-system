from functools import lru_cache
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Event-Driven Order Orchestration System"
    environment: str = "local"
    debug: bool = False
    secret_key: str = Field(default="change-me", min_length=8)
    access_token_expire_minutes: int = 60
    database_url: str = "sqlite:///./order_processing.db"
    test_database_url: str = "sqlite:///./test_order_processing.db"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "amqp://guest:guest@localhost:5672//"
    celery_result_backend: str = "redis://localhost:6379/1"
    rabbitmq_management_url: str = "http://localhost:15672"
    rabbitmq_default_user: str = "guest"
    rabbitmq_default_pass: str = "guest"
    log_level: str = "INFO"
    metrics_token: str | None = None

    @model_validator(mode="after")
    def validate_production_secret(self) -> Self:
        if self.environment not in {"local", "development", "test"} and (
            self.secret_key in {"change-me", "change-me-in-production"} or len(self.secret_key) < 32
        ):
            raise ValueError("Production secret_key must be at least 32 characters and not a known default")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
