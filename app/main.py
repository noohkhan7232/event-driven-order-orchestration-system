from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import DomainError
from app.core.logging import configure_logging
from app.middleware.request_context import RequestContextMiddleware
from app.schemas.common import ErrorResponse

configure_logging()
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
app.add_middleware(RequestContextMiddleware)
app.include_router(api_router)


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(code=exc.code, message=exc.message, details=exc.details).model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(code="internal_server_error", message="Unexpected server error").model_dump(),
    )
