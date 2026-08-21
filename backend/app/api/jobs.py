from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi import APIRouter, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.db.base import create_engine_for, session_factory
from app.db.models import Job
from app.settings.config import Settings


def create_jobs_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/jobs")
    active_settings = settings or Settings()

    @router.get("/snapshot")
    def snapshot(after: str | None = Query(default=None)) -> dict[str, list[dict[str, object]]]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        try:
            with factory() as session:
                return {"events": _job_events(session, after=after)}
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
                    for event in _job_events(session, after=cursor):
                        yield {
                            "id": str(event["sequenceId"]),
                            "event": "job",
                            "data": json.dumps(event, separators=(",", ":")),
                        }
            finally:
                engine.dispose()

        return EventSourceResponse(stream())

    return router


def _job_events(session: Session, *, after: str | None) -> list[dict[str, object]]:
    jobs = session.execute(select(Job).order_by(Job.updated_at.asc(), Job.id.asc())).scalars().all()
    events = [_to_event(job) for job in jobs]
    if after:
        normalized_after = after.replace(" ", "+")
        return [event for event in events if str(event["sequenceId"]) > normalized_after]
    return events


def _to_event(job: Job) -> dict[str, object]:
    return {
        "sequenceId": _sequence_id(job),
        "jobId": job.id,
        "status": job.status,
        "current": job.progress_current,
        "total": job.progress_total,
        "errorCode": job.error_code,
    }


def _sequence_id(job: Job) -> str:
    updated_at = job.updated_at
    if not isinstance(updated_at, datetime):
        updated_at = datetime.now(UTC)
    return f"{updated_at.astimezone(UTC).isoformat()}:{job.id}"
