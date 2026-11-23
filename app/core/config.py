from functools import lru_cache
from typing import Self

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

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
    outbox_max_attempts: int = Field(default=10, ge=1)
    outbox_poll_interval_seconds: float = Field(default=0.5, gt=0)
    outbox_retention_days: int = Field(default=7, ge=1)
    task_max_retries: int = Field(default=3, ge=0)
    task_retry_backoff_max: int = Field(default=60, ge=1)
    failure_injection_enabled: bool = False
    failure_transient_attempts: int = Field(default=2, ge=0)
    payment_provider: str = "mock"
    shipping_provider: str = "mock"
    allow_mock_providers: bool = False
    processed_events_retention_days: int = Field(default=30, ge=1)
    rate_limit_redis_url: str | None = None
    rate_limit_fail_open: bool = True
    trusted_proxy_count: int = Field(default=0, ge=0)
    rate_limit_login_ip_limit: int = Field(default=10, ge=1)
    rate_limit_login_ip_window_seconds: int = Field(default=60, ge=1)
    rate_limit_login_email_limit: int = Field(default=5, ge=1)
    rate_limit_login_email_window_seconds: int = Field(default=900, ge=1)
    rate_limit_register_limit: int = Field(default=5, ge=1)
    rate_limit_register_window_seconds: int = Field(default=3600, ge=1)
    rate_limit_signup_limit: int = Field(default=10, ge=1)
    rate_limit_signup_window_seconds: int = Field(default=3600, ge=1)
    rate_limit_authenticated_limit: int = Field(default=600, ge=1)
    rate_limit_authenticated_window_seconds: int = Field(default=60, ge=1)
    rate_limit_public_limit: int = Field(default=600, ge=1)
    rate_limit_public_window_seconds: int = Field(default=60, ge=1)
    rate_limit_webhook_limit: int = Field(default=600, ge=1)
    rate_limit_webhook_window_seconds: int = Field(default=60, ge=1)
    rate_limit_tracking_limit: int = Field(default=60, ge=1)
    rate_limit_tracking_window_seconds: int = Field(default=60, ge=1)
    sse_tenant_connection_limit: int = Field(default=10, ge=1)
    sse_ip_connection_limit: int = Field(default=5, ge=1)
    registration_enabled: bool = True
    registration_invite_code: SecretStr | None = Field(default=None, min_length=1)
    credentials_encryption_key: SecretStr | None = None
    mock_webhook_secret: SecretStr = SecretStr("local-mock-webhook")
    mock_payment_auto_complete: bool | None = None
    mock_refund_fail_attempts: int = Field(default=0, ge=0)
    payment_window_seconds: int = Field(default=1800, ge=1)
    payment_unmatched_retry_seconds: int = Field(default=600, ge=0)
    payment_capture_grace_seconds: int = Field(default=120, ge=0)
    payment_expiry_interval_seconds: float = Field(default=30, gt=0)
    payment_maintenance_batch_size: int = Field(default=100, ge=1, le=1000)
    payment_reconcile_after_seconds: int = Field(default=60, ge=0)
    payment_reconcile_interval_seconds: float = Field(default=60, gt=0)
    payment_reconcile_lease_seconds: int = Field(default=60, ge=1)
    refund_lease_seconds: int = Field(default=60, ge=1)
    webhook_allow_insecure_targets: bool = False
    webhook_extra_allowed_ports: list[int] = Field(default_factory=list)
    webhook_max_attempts: int = Field(default=8, ge=1, le=100)
    webhook_timeout_seconds: float = Field(default=5, gt=0, le=60)
    webhook_response_max_bytes: int = Field(default=1024, ge=1, le=65536)
    webhook_retry_base_seconds: float = Field(default=600, gt=0)
    webhook_retry_max_seconds: float = Field(default=43200, gt=0)
    webhook_disable_after_failures: int = Field(default=20, ge=1)
    webhook_delivery_interval_seconds: float = Field(default=10, gt=0)
    webhook_delivery_batch_size: int = Field(default=100, ge=1, le=1000)
    webhook_lease_seconds: int = Field(default=60, ge=1)
    public_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    notifications_backend: str = "console"
    notifications_allow_console: bool = False
    smtp_host: str = "mailpit"
    smtp_port: int = Field(default=1025, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str = "noreply@example.com"
    smtp_use_tls: bool = False
    smtp_timeout_seconds: float = Field(default=10, gt=0, le=60)
    notification_max_attempts: int = Field(default=4, ge=1, le=20)
    notification_retry_seconds: float = Field(default=60, gt=0)
    notification_lease_seconds: int = Field(default=120, ge=1)
    notification_batch_size: int = Field(default=100, ge=1, le=1000)
    notification_interval_seconds: float = Field(default=10, gt=0)
    tracking_token_secret: SecretStr = Field(default=SecretStr("local-tracking-token-secret-change-me"), min_length=8)
    public_tracking_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    public_tracking_show_total: bool = False

    @field_validator("credentials_encryption_key")
    @classmethod
    def validate_encryption_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            from app.core.crypto import credential_ring

            try:
                credential_ring(value)
            except (ValueError, UnicodeError):
                raise ValueError("CREDENTIALS_ENCRYPTION_KEY must contain valid Fernet keys") from None
        return value

    @field_validator("rate_limit_redis_url", mode="before")
    @classmethod
    def empty_redis_url(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def validate_production_secret(self) -> Self:
        local = self.environment in {"local", "development", "test"}
        if not local and self.webhook_allow_insecure_targets:
            raise ValueError("Insecure webhook targets are forbidden outside local/development/test")
        if any(port < 1 or port > 65535 for port in self.webhook_extra_allowed_ports):
            raise ValueError("Invalid webhook port allowlist")
        if self.webhook_lease_seconds <= self.webhook_timeout_seconds:
            raise ValueError("Webhook lease must exceed HTTP timeout")
        if self.mock_payment_auto_complete is None:
            self.mock_payment_auto_complete = local
        if not local and self.mock_payment_auto_complete:
            raise ValueError("Mock auto-complete is forbidden outside local/development/test")
        if self.failure_injection_enabled and self.environment not in {"local", "development", "test"}:
            raise ValueError("Failure injection is forbidden outside local/development/test")
        if self.environment not in {"local", "development", "test"} and (
            self.secret_key in {"change-me", "change-me-in-production"} or len(self.secret_key) < 32
        ):
            raise ValueError("Production secret_key must be at least 32 characters and not a known default")
        if self.payment_provider != "mock" or self.shipping_provider not in {"mock", "manual"}:
            raise ValueError("Unknown provider")
        if self.environment not in {"local", "development", "test"} and not self.allow_mock_providers:
            raise ValueError("Non-local mock providers require ALLOW_MOCK_PROVIDERS=true")
        if self.environment not in {"local", "development", "test"} and self.credentials_encryption_key is None:
            raise ValueError("CREDENTIALS_ENCRYPTION_KEY is required outside local/development/test")
        if not local and self.shipping_provider == "mock":
            raise ValueError("mock shipping is forbidden outside local/development/test")
        if self.notifications_backend not in {"console", "smtp"}:
            raise ValueError("Unknown notifications backend")
        if not local and self.notifications_backend == "console" and not self.notifications_allow_console:
            raise ValueError("Production notifications require smtp or explicit console opt-out")
        if not self.smtp_host or any(c in self.smtp_from for c in "\r\n") or "@" not in self.smtp_from:
            raise ValueError("Invalid SMTP host/from configuration")
        if self.notification_lease_seconds <= self.smtp_timeout_seconds:
            raise ValueError("Notification lease must exceed SMTP timeout")
        if not local and (
            self.tracking_token_secret.get_secret_value() == "local-tracking-token-secret-change-me"
            or len(self.tracking_token_secret.get_secret_value()) < 32
        ):
            raise ValueError("TRACKING_TOKEN_SECRET must be a strong non-default secret outside local/development/test")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
