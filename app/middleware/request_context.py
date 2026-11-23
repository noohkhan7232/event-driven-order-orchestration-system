import re
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.observability.metrics import REQUEST_COUNT, REQUEST_LATENCY


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", incoming) else str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        request.state.request_id = request_id
        try:
            response = await call_next(request)
            route_path = getattr(request.scope.get("route"), "path", "unmatched")
            duration = time.perf_counter() - start
            response.headers["x-request-id"] = request_id
            response.headers["x-response-time-ms"] = f"{duration * 1000:.2f}"
            REQUEST_COUNT.labels(request.method, route_path, response.status_code).inc()
            REQUEST_LATENCY.labels(request.method, route_path).observe(duration)
            structlog.get_logger("http").info(
                "request_completed",
                method=request.method,
                path="/api/v1/public/track/{token}"
                if request.url.path.startswith("/api/v1/public/track/")
                else request.url.path,
                status=response.status_code,
                duration_ms=round(duration * 1000, 2),
            )
            return response
        finally:
            structlog.contextvars.clear_contextvars()
