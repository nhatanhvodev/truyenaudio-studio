from __future__ import annotations

from collections.abc import Iterator
import json

from fastapi import APIRouter, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.db.base import create_engine_for, session_factory
from app.db.models import Job
from app.settings.config import Settings


def create_events_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/events")
    active_settings = settings or Settings()

    @router.get("")
    def events(
        after: int | None = Query(default=None),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> EventSourceResponse:
        cursor = _parse_cursor(last_event_id) if last_event_id is not None else after

        def stream() -> Iterator[dict[str, str]]:
            engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
            factory = session_factory(engine)
            try:
                with factory() as session:
                    for event in _events(session, after=cursor):
                        yield {
                            "id": str(event["sequenceId"]),
                            "event": str(event["type"]),
                            "data": json.dumps(event, separators=(",", ":")),
                        }
            finally:
                engine.dispose()

        return EventSourceResponse(stream())

    return router


def _events(session: Session, *, after: int | None) -> list[dict[str, object]]:
    jobs = session.execute(select(Job).order_by(Job.updated_at.asc(), Job.id.asc())).scalars().all()
    events = [
        {
            "sequenceId": index,
            "type": "job",
            "jobId": job.id,
            "status": job.status,
            "current": job.progress_current,
            "total": job.progress_total,
            "errorCode": job.error_code,
        }
        for index, job in enumerate(jobs, start=1)
    ]
    if after is None:
        return events
    return [event for event in events if int(event["sequenceId"]) > after]


def _parse_cursor(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
