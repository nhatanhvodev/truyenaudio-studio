from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.security import create_security_router, install_csrf_middleware
from app.api.storage import create_storage_router
from app.api.batches import create_batches_router
from app.api.cloud_consents import create_cloud_consents_router
from app.api.cloud_profiles import create_cloud_profiles_router
from app.api.audio import create_audio_router
from app.api.asr_qa import create_asr_qa_router
from app.api.diagnostics import create_diagnostics_router
from app.api.events import create_events_router
from app.api.exports import create_exports_router
from app.api.health import create_health_router
from app.api.glossary import create_glossary_router
from app.api.poc import create_poc_router
from app.api.jobs import create_jobs_router
from app.api.projects import create_projects_router
from app.api.review import create_review_router
from app.api.rights import create_rights_router
from app.api.translation import create_translation_router
from app.api.voice_plans import create_voice_plans_router
from app.api.voices import create_voices_router
from app.settings.config import Settings
from app.settings.csrf import CsrfService
from app.settings.startup_lock import StartupLock


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


def create_app(
    *,
    settings: Settings | None = None,
    frontend_dist: Path | None = None,
    acquire_lock: bool = True,
) -> FastAPI:
    active_settings = settings or Settings()
    api_lock = StartupLock(active_settings.data_root / "studio-api.lock")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if acquire_lock:
            api_lock.acquire()
        try:
            yield
        finally:
            if acquire_lock:
                api_lock.release()

    app = FastAPI(lifespan=lifespan)
    csrf = CsrfService()
    install_csrf_middleware(app, csrf)
    app.include_router(create_security_router(csrf))
    app.include_router(create_health_router())
    app.include_router(create_poc_router())
    app.include_router(create_batches_router(active_settings))
    app.include_router(create_jobs_router(active_settings))
    app.include_router(create_projects_router(active_settings, cursor_secret=csrf.token))
    app.include_router(create_glossary_router(active_settings))
    app.include_router(create_translation_router(active_settings))
    app.include_router(create_review_router(active_settings))
    app.include_router(create_rights_router(active_settings))
    app.include_router(create_cloud_consents_router(active_settings))
    app.include_router(create_cloud_profiles_router(active_settings))
    app.include_router(create_voices_router(active_settings))
    app.include_router(create_voice_plans_router(active_settings))
    app.include_router(create_audio_router(active_settings))
    app.include_router(create_asr_qa_router())
    app.include_router(create_exports_router(active_settings))
    app.include_router(create_storage_router(active_settings))
    app.include_router(create_events_router(active_settings))
    app.include_router(create_diagnostics_router(active_settings))
    _register_frontend(app, frontend_dist or DEFAULT_FRONTEND_DIST)
    return app


def _register_frontend(app: FastAPI, frontend_dist: Path) -> None:
    index_path = frontend_dist / "index.html"
    assets_path = frontend_dist / "assets"
    if assets_path.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_path), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def frontend_root() -> FileResponse:
        if not index_path.is_file():
            raise HTTPException(status_code=404, detail="frontend build missing")
        return FileResponse(index_path)

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        dist_root = frontend_dist.resolve()
        candidate = (dist_root / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(dist_root):
            return FileResponse(candidate)
        if not index_path.is_file():
            raise HTTPException(status_code=404, detail="frontend build missing")
        return FileResponse(index_path)


app = create_app()
