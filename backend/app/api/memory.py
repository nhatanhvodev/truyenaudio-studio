from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.db.base import create_engine_for, session_factory
from app.modules.translation.context_engine import ContextItem, ContextSelection, select_context
from app.modules.translation.memory_index import (
    MemoryIndexService,
    VectorQuery,
    project_memory_snapshot_hash,
)
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


class EmbeddingIndexImportRequest(BaseModel):
    """A user-supplied embedding artifact (X06).

    Envelope fields are typed; the entries stay opaque on purpose so every
    rejection - unknown schema, mismatched dimension, NaN/Infinity, empty vector,
    missing id, oversized index, undeclared license - is answered with its own
    machine-readable code instead of a generic request-validation error.
    """

    model_config = ConfigDict(extra="allow")

    schemaVersion: int
    dimension: int
    modelId: str
    modelRevision: str | None = None
    sourceHash: str | None = None
    license: str | None = None
    disabled: bool = False
    vectors: list[dict[str, object]]


class EmbeddingQueryRequest(BaseModel):
    """The query embedding, supplied by the caller - never generated here."""

    embedding: list[float]
    modelId: str
    dimension: int | None = None
    modelRevision: str | None = None
    queryHash: str | None = None


class EmbeddingRetrieveRequest(BaseModel):
    ordinal: int | None = None
    tokenBudget: int = 1200
    query: EmbeddingQueryRequest | None = None


def create_memory_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/memory")
    active_settings = settings or Settings()

    def service_dependency() -> Iterator[StoryMemoryService]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield StoryMemoryService(session)
        engine.dispose()

    def index_service_dependency() -> Iterator[MemoryIndexService]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield MemoryIndexService(session)
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

    @router.post("/embedding-index/import")
    def import_embedding_index(
        project_id: str,
        request: EmbeddingIndexImportRequest,
        service: MemoryIndexService = Depends(index_service_dependency),
    ) -> dict[str, object]:
        """Store a validated, user-supplied embedding artifact.

        The studio never creates an embedding: this route only persists what the
        owner imported, after checking schema, dimension, values, license and the
        entry/byte budget.
        """
        try:
            index = service.import_artifact(project_id, request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"index": index}

    @router.get("/embedding-index/status")
    def read_embedding_index_status(
        project_id: str,
        service: MemoryIndexService = Depends(index_service_dependency),
    ) -> dict[str, object]:
        try:
            return {"index": service.status(project_id)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/embedding-index/rebuild")
    def rebuild_embedding_index(
        project_id: str,
        service: MemoryIndexService = Depends(index_service_dependency),
    ) -> dict[str, object]:
        """Re-derive the index from the stored artifact; no new embedding is made."""
        try:
            index = service.rebuild(project_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"index": index}

    @router.post("/embedding-index/retrieve")
    def retrieve_with_embedding_index(
        project_id: str,
        request: EmbeddingRetrieveRequest,
        memory: StoryMemoryService = Depends(service_dependency),
        index_service: MemoryIndexService = Depends(index_service_dependency),
    ) -> dict[str, object]:
        """Cosine top-8 of the project's index, decorated onto the structured selection.

        Without a usable index - or without a query embedding that matches its
        model and dimension - the structured selection is returned untouched and
        the trace says why.
        """
        ordinal = request.ordinal if request.ordinal is not None else 1
        query = (
            VectorQuery(
                embedding=tuple(request.query.embedding),
                model_id=request.query.modelId,
                dimension=request.query.dimension,
                model_revision=request.query.modelRevision,
                query_hash=request.query.queryHash,
            )
            if request.query is not None
            else None
        )
        retrieval = index_service.retrieve(project_id, query=query, ordinal=ordinal)
        items = tuple(
            ContextItem(id=entry.id, kind=entry.entity_type, text=entry.summary)
            for entry in memory.memory_for(project_id, ordinal)
        )
        structured = select_context(items, token_budget=request.tokenBudget)
        decorated = select_context(
            items,
            token_budget=request.tokenBudget,
            vector=retrieval.hint(),
        )
        return {
            "index": index_service.status(project_id),
            "retrieval": retrieval.trace(),
            "structured": _selection_payload(structured),
            "selection": _selection_payload(decorated),
            "memorySnapshotHash": project_memory_snapshot_hash(index_service.session, project_id),
        }

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


def _selection_payload(selection: ContextSelection) -> dict[str, object]:
    return {
        "selected": [item.id for item in selection.selected],
        "excluded": [{"id": item_id, "reason": reason} for item_id, reason in selection.excluded],
        "totalTokens": selection.total_tokens,
        "sha256": selection.sha256,
        "vectorUsed": bool(selection.vector is not None and selection.vector.used),
        "vectorReason": selection.vector.reason if selection.vector is not None else None,
    }
