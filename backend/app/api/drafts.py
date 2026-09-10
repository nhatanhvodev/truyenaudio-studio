from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.db.base import create_engine_for, session_factory
from app.modules.translation.draft_service import draft_frames, draft_snapshot
from app.modules.translation.drafts import DraftConflict, restore_draft, save_draft
from app.settings.config import Settings


class DraftSaveRequest(BaseModel):
    base_revision_id: str
    content: dict[str, str]
    expected_revision: int | None = None


def create_drafts_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter()
    active_settings = settings or Settings()

    def session_dependency() -> Iterator[object]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield session
        engine.dispose()

    @router.get("/api/jobs/{job_id}/draft")
    def read_draft(
        job_id: str,
        after_offset: int | None = Query(default=None, alias="afterOffset", ge=0),
        session: object = Depends(session_dependency),
    ) -> dict[str, object]:
        try:
            return draft_snapshot(session, job_id, after_offset)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/jobs/{job_id}/draft/stream")
    def stream_draft(
        job_id: str,
        session: object = Depends(session_dependency),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        after_offset: int | None = Query(default=None, alias="afterOffset", ge=0),
    ) -> EventSourceResponse:
        try:
            frames = draft_frames(session, job_id)
            snapshot = draft_snapshot(session, job_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        cursor = after_offset
        if cursor is None and last_event_id is not None:
            try:
                cursor = int(last_event_id)
            except ValueError:
                cursor = None

        return EventSourceResponse(build_stream_events(frames, snapshot, cursor))

    @router.get("/api/chapters/{chapter_id}/draft")
    def read_chapter_draft(
        chapter_id: str,
        base_revision_id: str = Query(alias="baseRevisionId"),
        session: object = Depends(session_dependency),
    ) -> dict[str, object]:
        draft = restore_draft(session, chapter_id, base_revision_id)
        return {"draft": asdict(draft) if draft is not None else None}

    @router.put("/api/chapters/{chapter_id}/draft")
    def save_chapter_draft(
        chapter_id: str,
        request: DraftSaveRequest,
        session: object = Depends(session_dependency),
    ) -> dict[str, object]:
        try:
            draft = save_draft(
                session,
                chapter_id,
                request.base_revision_id,
                request.content,
                expected_revision=request.expected_revision,
            )
        except DraftConflict as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"draft": asdict(draft)}

    return router


def build_stream_events(
    frames: tuple[dict[str, object], ...],
    snapshot: dict[str, object],
    cursor: int | None,
) -> Iterator[dict[str, str]]:
    """Pure SSE event sequence for the draft feed (unit-testable).

    Emits a ``gap`` snapshot first when the client cursor is not a known frame
    boundary, then the frames after the cursor, then a ``terminal`` event.
    """
    boundaries = {int(frame["offset"]) for frame in frames}
    if cursor is not None and cursor != 0 and cursor not in boundaries and cursor < int(snapshot["offset"]):
        yield {
            "event": "gap",
            "id": str(snapshot["offset"]),
            "data": json.dumps(snapshot, ensure_ascii=False),
        }
    for frame in frames:
        offset = int(frame["offset"])
        if cursor is not None and offset <= cursor:
            continue
        yield {
            "event": "draft",
            "id": str(offset),
            "data": json.dumps({"offset": offset, "text": frame["text"]}, ensure_ascii=False),
        }
    yield {
        "event": "terminal",
        "id": str(snapshot["offset"]),
        "data": json.dumps(
            {"status": snapshot["status"], "approvable": snapshot["approvable"]},
            ensure_ascii=False,
        ),
    }
