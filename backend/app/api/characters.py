from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db.base import create_engine_for, session_factory
from app.modules.translation.characters import (
    CharacterAliasAmbiguous,
    CharacterService,
    RelationshipCommand,
)
from app.settings.config import Settings


class CharacterUpsertRequest(BaseModel):
    canonical_name: str
    entity_type: str
    aliases: list[str] = []
    role: str | None = None
    gender: str | None = None


class CharacterApproveRequest(BaseModel):
    evidence_source_revision_id: str
    evidence_segment_ids: list[str]


class RelationshipCreateRequest(BaseModel):
    from_character_id: str
    to_character_id: str
    from_ordinal: int
    to_ordinal: int | None = None
    addressing: dict[str, str] | None = None
    evidence_source_revision_id: str | None = None


def create_characters_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/characters")
    active_settings = settings or Settings()

    def service_dependency() -> Iterator[CharacterService]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield CharacterService(session)
        engine.dispose()

    @router.get("")
    def read_characters(
        project_id: str, service: CharacterService = Depends(service_dependency)
    ) -> dict[str, object]:
        return {
            "characters": [
                asdict(view) for view in service.active_characters(project_id)
            ]
        }

    @router.post("")
    def upsert_character(
        project_id: str,
        request: CharacterUpsertRequest,
        service: CharacterService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            view = service.create_or_revise(
                project_id,
                canonical_name=request.canonical_name,
                entity_type=request.entity_type,
                aliases=tuple(request.aliases),
                role=request.role,
                gender=request.gender,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"character": asdict(view)}

    @router.post("/{character_id}/approve")
    def approve_character(
        project_id: str,
        character_id: str,
        request: CharacterApproveRequest,
        service: CharacterService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            view = service.approve(
                project_id,
                character_id,
                evidence_source_revision_id=request.evidence_source_revision_id,
                evidence_segment_ids=tuple(request.evidence_segment_ids),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"character": asdict(view)}

    @router.get("/resolve")
    def resolve_alias(
        project_id: str,
        alias: str = Query(...),
        service: CharacterService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            view = service.resolve_alias(project_id, alias)
        except CharacterAliasAmbiguous as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"character": asdict(view)}

    @router.get("/relationships")
    def read_relationships(
        project_id: str,
        ordinal: int = Query(..., ge=1),
        service: CharacterService = Depends(service_dependency),
    ) -> dict[str, object]:
        return {
            "relationships": [
                asdict(view)
                for view in service.active_relationships(project_id, ordinal)
            ]
        }

    @router.post("/relationships")
    def create_relationship(
        project_id: str,
        request: RelationshipCreateRequest,
        service: CharacterService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            view = service.add_relationship(
                project_id,
                RelationshipCommand(
                    from_character_id=request.from_character_id,
                    to_character_id=request.to_character_id,
                    from_ordinal=request.from_ordinal,
                    to_ordinal=request.to_ordinal,
                    addressing=request.addressing,
                    evidence_source_revision_id=request.evidence_source_revision_id,
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"relationship": asdict(view)}

    return router
