import logging
import re
import sys

import structlog

from app.core.config import get_settings


class TrackingLogFilter(logging.Filter):
    """Redact bearer links in server and HTTP-client access logging."""

    def filter(self, record: logging.LogRecord) -> bool:
        def redact(value):
            text = str(value)
            if "/api/v1/public/track/" not in text:
                return value
            return re.sub(r"""(/api/v1/public/track/)[^\s"'<>]+""", r"\1{token}", text)

        record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: redact(value) for key, value in record.args.items()}
        return True


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=settings.log_level.upper())
    for name in ("uvicorn.access", "httpx"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, TrackingLogFilter) for item in logger.filters):
            logger.addFilter(TrackingLogFilter())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(settings.log_level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):
    return structlog.get_logger(name)
