from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Header, Query
from sse_starlette.sse import EventSourceResponse

from app.api.events import _events, _parse_cursor
from app.db.base import create_engine_for, session_factory
from app.settings.config import Settings
import json


def create_jobs_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/jobs")
    active_settings = settings or Settings()

    @router.get("/snapshot")
    def snapshot(after: str | None = Query(default=None)) -> dict[str, list[dict[str, object]]]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        try:
            with factory() as session:
                return {"events": _job_events_from_durable_log(session, after=after)}
        finally:
            engine.dispose()

    @router.get("/events")
    def events(
        after: str | None = Query(default=None),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> EventSourceResponse:
        cursor = last_event_id or after

        def stream() -> Iterator[dict[str, str]]:
            engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
            factory = session_factory(engine)
            try:
                with factory() as session:
                    for event in _job_events_from_durable_log(session, after=cursor):
                        yield {
                            "id": str(event["sequenceId"]),
                            "event": "job",
                            "data": json.dumps(event, separators=(",", ":")),
                        }
            finally:
                engine.dispose()

        return EventSourceResponse(stream())

    return router


def _job_events_from_durable_log(session, *, after: str | None) -> list[dict[str, object]]:
    cursor = _parse_cursor(after) if after else None
    return [event for event in _events(session, after=cursor) if event["type"] == "job"]
