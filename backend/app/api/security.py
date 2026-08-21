from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException, Request
from starlette.responses import JSONResponse

from app.settings.csrf import CsrfService, STATE_CHANGING_METHODS


def create_security_router(csrf: CsrfService) -> APIRouter:
    router = APIRouter(prefix="/api/security")

    @router.get("/bootstrap")
    def bootstrap(request: Request) -> dict[str, str]:
        if not csrf.allows_bootstrap(request):
            raise HTTPException(status_code=403, detail="LOOPBACK_ORIGIN_REQUIRED")
        return {"csrfToken": csrf.token}

    return router


def install_csrf_middleware(app: FastAPI, csrf: CsrfService) -> None:
    @app.middleware("http")
    async def require_csrf(request: Request, call_next):
        if request.method in STATE_CHANGING_METHODS and not csrf.validates_state_change(request):
            return JSONResponse({"detail": "CSRF_ORIGIN_TOKEN_REQUIRED"}, status_code=403)
        return await call_next(request)
