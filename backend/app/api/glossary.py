from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.base import create_engine_for, session_factory
from app.modules.translation.glossary import (
    GlossaryCommand,
    GlossaryService,
    active_glossary,
)
from app.settings.config import Settings


class GlossaryUpsertRequest(BaseModel):
    source_term: str
    target_term: str
    reading: str | None = None
    category: str | None = None
    gender: str | None = None
    addressing_notes: str | None = None
    is_locked: bool = False
    description: str | None = None
    forbidden_forms: list[str] = []
    evidence: str | None = None
    scope_from_ordinal: int | None = None
    scope_to_ordinal: int | None = None


def create_glossary_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/glossary")
    active_settings = settings or Settings()

    def service_dependency() -> Iterator[GlossaryService]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield GlossaryService(session)
        engine.dispose()

    @router.get("")
    def read_active_glossary(
        project_id: str, service: GlossaryService = Depends(service_dependency)
    ) -> dict[str, object]:
        return {"revision": asdict(active_glossary(service.session, project_id))}

    @router.post("")
    def upsert_glossary_entry(
        project_id: str,
        request: GlossaryUpsertRequest,
        service: GlossaryService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            result = service.upsert(
                project_id,
                GlossaryCommand(
                    request.source_term,
                    request.target_term,
                    request.reading,
                    request.category,
                    request.gender,
                    request.addressing_notes,
                    request.is_locked,
                    description=request.description,
                    forbidden_forms=tuple(request.forbidden_forms or ()),
                    evidence=request.evidence,
                    scope_from_ordinal=request.scope_from_ordinal,
                    scope_to_ordinal=request.scope_to_ordinal,
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(result)

    return router
