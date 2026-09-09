from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db.base import create_engine_for, session_factory
from app.modules.translation.story_memory import StoryMemoryService
from app.settings.config import Settings


class MemoryCandidateRequest(BaseModel):
    entity_key: str
    entity_type: str = "FACT"
    summary: str
    valid_from_ordinal: int
    valid_to_ordinal: int | None = None


class MemoryApproveRequest(BaseModel):
    source_run_id: str
    evidence_segment_ids: list[str]


def create_memory_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/memory")
    active_settings = settings or Settings()

    def service_dependency() -> Iterator[StoryMemoryService]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield StoryMemoryService(session)
        engine.dispose()

    @router.get("/context")
    def read_approved_context(
        project_id: str,
        ordinal: int = Query(..., ge=1),
        service: StoryMemoryService = Depends(service_dependency),
    ) -> dict[str, object]:
        return {
            "entries": [asdict(entry) for entry in service.memory_for(project_id, ordinal)],
            "sha256": service.hash_for(project_id, ordinal),
        }

    @router.get("/candidates")
    def read_candidates(
        project_id: str, service: StoryMemoryService = Depends(service_dependency)
    ) -> dict[str, object]:
        return {"candidates": [asdict(entry) for entry in service.candidates_for(project_id)]}

    @router.post("/candidates")
    def create_candidate(
        project_id: str,
        request: MemoryCandidateRequest,
        service: StoryMemoryService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            entry = service.create_candidate(
                project_id,
                entity_key=request.entity_key,
                entity_type=request.entity_type,
                summary=request.summary,
                valid_from_ordinal=request.valid_from_ordinal,
                valid_to_ordinal=request.valid_to_ordinal,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"entry": asdict(entry)}

    @router.post("/{memory_id}/approve")
    def approve_candidate(
        project_id: str,
        memory_id: str,
        request: MemoryApproveRequest,
        service: StoryMemoryService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            entry = service.approve(
                project_id,
                memory_id,
                source_run_id=request.source_run_id,
                evidence_segment_ids=tuple(request.evidence_segment_ids),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"entry": asdict(entry)}

    @router.post("/{memory_id}/reject")
    def reject_candidate(
        project_id: str,
        memory_id: str,
        service: StoryMemoryService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            entry = service.reject(project_id, memory_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"entry": asdict(entry)}

    return router
