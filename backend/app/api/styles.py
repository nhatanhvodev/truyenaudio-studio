from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.base import create_engine_for, session_factory
from app.modules.translation.style_profile import (
    DEFAULT_LANGUAGES,
    PRESETS,
    PRESETS_BY_KEY,
    StyleCommand,
    StyleProfileService,
    active_styles,
)
from app.settings.config import Settings


class StyleUpsertRequest(BaseModel):
    name: str
    genre: str
    tone: str
    source_language: str | None = None
    target_language: str | None = None
    user_instruction: str = ""


def create_styles_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/styles")
    active_settings = settings or Settings()

    def service_dependency() -> Iterator[StyleProfileService]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield StyleProfileService(session)
        engine.dispose()

    @router.get("")
    def read_active_styles(
        project_id: str, service: StyleProfileService = Depends(service_dependency)
    ) -> dict[str, object]:
        return {"styles": [asdict(view) for view in active_styles(service.session, project_id)]}

    @router.get("/presets")
    def list_presets() -> dict[str, object]:
        return {"presets": [asdict(preset) for preset in PRESETS]}

    @router.post("")
    def upsert_style(
        project_id: str,
        request: StyleUpsertRequest,
        service: StyleProfileService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            source_language, target_language = _languages(request)
            snapshot = service.upsert(
                project_id,
                StyleCommand(
                    request.name,
                    request.genre,
                    request.tone,
                    source_language=source_language,
                    target_language=target_language,
                    user_instruction=request.user_instruction,
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"style": asdict(snapshot.style), "sha256": snapshot.sha256}

    return router


def _languages(request: StyleUpsertRequest) -> tuple[str, str]:
    if request.source_language and request.target_language:
        return request.source_language, request.target_language
    if request.name.strip() in PRESETS_BY_KEY:
        return DEFAULT_LANGUAGES
    raise HTTPException(status_code=400, detail="STYLE_LANGUAGE_REQUIRED")
