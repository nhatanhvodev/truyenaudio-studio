from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contracts import JobKind
from app.db.base import create_engine_for
from app.modules.jobs.batch import BatchCoordinator
from app.settings.config import Settings


class EnqueueBatchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(alias="projectId")
    chapter_ids: tuple[str, ...] = Field(alias="chapterIds")
    stage: JobKind
    quote_id: str | None = Field(default=None, alias="quoteId")


def create_batches_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/batches")
    active_settings = settings or Settings()

    def coordinator_dependency() -> Iterator[BatchCoordinator]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        try:
            yield BatchCoordinator(engine)
        finally:
            engine.dispose()

    @router.post("")
    def enqueue_batch(
        request: EnqueueBatchRequest,
        coordinator: BatchCoordinator = Depends(coordinator_dependency),
    ) -> dict[str, object]:
        try:
            return _camelize(_convert(asdict(coordinator.enqueue_batch(
                request.project_id,
                request.chapter_ids,
                request.stage,
                request.quote_id,
            ))))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


def _convert(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _convert(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_convert(item) for item in value]
    return value


def _camelize(value: object) -> object:
    if isinstance(value, dict):
        return {_camel_key(key): _camelize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


def _camel_key(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)
