from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.db.base import create_engine_for, session_factory
from app.modules.translation.review import ReviewService
from app.settings.config import Settings


class EnqueueReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    selected_segment_ids: tuple[str, ...] = Field(default=(), alias="selectedSegmentIds")


def create_review_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/chapters/{chapter_id}/review")
    active_settings = settings or Settings()

    def service_dependency() -> Iterator[ReviewService]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield ReviewService(session)
        engine.dispose()

    @router.post("/enqueue")
    def enqueue_review(
        chapter_id: str,
        request: EnqueueReviewRequest,
        service: ReviewService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            jobs = service.enqueue(chapter_id, selected_ids=request.selected_segment_ids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"jobs": [_camelize(_convert(asdict(job))) for job in jobs]}

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
