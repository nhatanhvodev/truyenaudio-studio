from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.routing import Match
from starlette.responses import JSONResponse
from urllib.parse import parse_qsl, unquote

from app.settings.csrf import CsrfService, LOOPBACK_HOST, STATE_CHANGING_METHODS


def create_security_router(csrf: CsrfService) -> APIRouter:
    router = APIRouter(prefix="/api/security")

    @router.get("/bootstrap")
    def bootstrap(request: Request) -> dict[str, str]:
        if not csrf.allows_bootstrap(request):
            raise HTTPException(status_code=403, detail="LOOPBACK_ORIGIN_REQUIRED")
        return {"csrfToken": csrf.token}

    return router


def install_csrf_middleware(app: FastAPI, csrf: CsrfService) -> None:
    @app.exception_handler(RequestValidationError)
    async def invalid_api_request(request: Request, _exc: RequestValidationError):
        return JSONResponse({"detail": "INVALID_REQUEST"}, status_code=422)

    @app.middleware("http")
    async def require_csrf(request: Request, call_next):
        if _targets_api_boundary(request):
            if not _is_canonical_api_request(app, request):
                return JSONResponse({"detail": "API_ROUTE_NOT_FOUND"}, status_code=404)
            if request.headers.get("host") != LOOPBACK_HOST:
                return JSONResponse({"detail": "LOOPBACK_HOST_REQUIRED"}, status_code=403)
        if request.method in STATE_CHANGING_METHODS and not csrf.validates_state_change(request):
            return JSONResponse({"detail": "CSRF_ORIGIN_TOKEN_REQUIRED"}, status_code=403)
        return await call_next(request)


def _is_canonical_api_request(app: FastAPI, request: Request) -> bool:
    """Allow only a registered API endpoint; the SPA catch-all must never handle API paths."""
    raw_path = request.scope.get("raw_path", b"")
    if not isinstance(raw_path, bytes):
        return False
    if b"%" in raw_path or b"\\" in raw_path or b".." in raw_path:
        return False
    if any(_is_forbidden_query_key(key) for key, _value in parse_qsl(request.url.query, keep_blank_values=True)):
        return False
    return any(
        getattr(route, "path", "").startswith("/api")
        and route.matches(request.scope)[0] is Match.FULL
        for route in app.router.routes
    )


def _targets_api_boundary(request: Request) -> bool:
    raw_path = request.scope.get("raw_path", b"")
    candidates = [request.url.path]
    if isinstance(raw_path, bytes):
        candidates.append(raw_path.decode("latin-1"))
    return any(_is_api_path(candidate) for candidate in candidates)


def _is_api_path(path: str) -> bool:
    normalized = _decode_percent_repeatedly(path).replace("\\", "/").casefold()
    return normalized == "/api" or normalized.startswith("/api/")


def _decode_percent_repeatedly(value: str) -> str:
    for _round in range(4):
        decoded = unquote(value)
        if decoded == value:
            return decoded
        value = decoded
    return value


def _is_forbidden_query_key(key: str) -> bool:
    return "%" in key or _is_secret_query_key(key)


def _is_secret_query_key(key: str) -> bool:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    if normalized in {"key", "token"}:
        return True
    # Recognize normalized vendor/header forms (x-goog-api-key,
    # google_api_key, authorization_header) while deliberately not treating
    # generic pagination identifiers such as cursor or id as credentials.
    return normalized.endswith("token") or any(
        part in normalized
        for part in {
            "apikey",
            "accesskey",
            "authorization",
            "credential",
            "secret",
            "password",
            "bearer",
        }
    )
