import json
from collections.abc import Sequence

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Match
from starlette.types import ASGIApp

from app.core.config import get_settings
from app.core.ratelimit import RateLimitError, client_ip, enforce, policies_for
from app.schemas.common import ErrorResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, routes: Sequence[BaseRoute]) -> None:
        super().__init__(app)
        self.routes = routes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Honor FastAPI settings overrides as well as normal environment settings.
        override = request.app.dependency_overrides.get(get_settings)
        settings = override() if override else get_settings()
        for route in self.routes:
            match, child_scope = route.matches(request.scope)
            if match != Match.FULL:
                continue
            policies = policies_for(request.method, getattr(route, "path", ""), settings)
            if policies:
                request.scope["route"] = route
                request.scope.update(child_scope)
                ip = client_ip(request, settings.trusted_proxy_count)
                for policy in policies:
                    email = ""
                    if policy.key_function == "email_ip":
                        # IP policy runs first, including malformed/oversized bodies.
                        body = bytearray()
                        async for chunk in request.stream():
                            body.extend(chunk)
                            if len(body) > 16384:
                                return JSONResponse(
                                    status_code=413,
                                    content=ErrorResponse(
                                        code="payload_too_large", message="Request body too large", details={}
                                    ).model_dump(),
                                )
                        # Starlette's cached request replays this bounded body to FastAPI.
                        request._body = bytes(body)
                        try:
                            payload = json.loads(body)
                            value = payload.get("email", "") if isinstance(payload, dict) else ""
                            email = value.strip().lower() if isinstance(value, str) else ""
                        except (ValueError, UnicodeError):
                            pass
                    identities = {
                        "ip": ip,
                        "email_ip": json.dumps([email, ip], separators=(",", ":")),
                        "webhook_id": str(request.path_params.get("webhook_public_id", "")),
                    }
                    try:
                        await run_in_threadpool(enforce, request, settings, policy, identities[policy.key_function])
                    except RateLimitError as exc:
                        safe_headers = (
                            {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
                            if policy.name == "tracking"
                            else {}
                        )
                        return JSONResponse(
                            status_code=exc.status_code,
                            headers={**exc.headers, **safe_headers},
                            content=ErrorResponse(code=exc.code, message=exc.message, details=exc.details).model_dump(),
                        )
            break
        response = await call_next(request)
        for name, value in getattr(request.state, "rate_limit_headers", {}).items():
            response.headers[name] = value
        return response
