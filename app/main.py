from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import DomainError
from app.core.logging import configure_logging, get_logger
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.schemas.common import ErrorResponse

configure_logging()
logger = get_logger(__name__)
settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "Enterprise-grade distributed order processing backend with event-driven workflows, "
        "inventory reservation, payment orchestration, fulfillment, retries, and observability."
    ),
    openapi_url="/api/v1/openapi.json",
    docs_url="/docs",
)
app.include_router(api_router)
app.add_middleware(RateLimitMiddleware, routes=app.routes)
app.add_middleware(RequestContextMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # A missing-field error can carry the entire body, including valid secrets
    # from other fields. Preserve locations/messages, never raw validation input.
    errors = [{key: value for key, value in error.items() if key not in {"input", "ctx"}} for error in exc.errors()]
    return JSONResponse(status_code=422, content=jsonable_encoder({"detail": errors}))


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError):
    return JSONResponse(
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
        content=ErrorResponse(code=exc.code, message=exc.message, details=exc.details).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.exception(
        "unhandled_exception",
        request_id=getattr(request.state, "request_id", None),
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(
        status_code=500,
        headers={"x-request-id": getattr(request.state, "request_id", str(uuid4()))},
        content=ErrorResponse(code="internal_server_error", message="Unexpected server error").model_dump(),
    )
