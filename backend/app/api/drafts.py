from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.base import create_engine_for, session_factory
from app.modules.translation.draft_service import draft_snapshot
from app.settings.config import Settings


def create_drafts_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/jobs")
    active_settings = settings or Settings()

    def session_dependency() -> Iterator[object]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield session
        engine.dispose()

    @router.get("/{job_id}/draft")
    def read_draft(
        job_id: str,
        after_offset: int | None = Query(default=None, alias="afterOffset", ge=0),
        session: object = Depends(session_dependency),
    ) -> dict[str, object]:
        try:
            return draft_snapshot(session, job_id, after_offset)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
