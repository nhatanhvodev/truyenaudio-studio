from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
import json

from fastapi import APIRouter, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.db.base import create_engine_for, session_factory
from app.db.models import AuditEvent, EventLog, Job, UsageLedger
from app.modules.diagnostics.logging import scrub_text
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
    _sync_event_log(session)
    rows = session.execute(select(EventLog).order_by(EventLog.sequence_id.asc())).scalars().all()
    events = [_event_payload(session, row) for row in rows]
    events = [event for event in events if event is not None]
    if after is None:
        return events
    return [event for event in events if int(event["sequenceId"]) > after]


def _sync_event_log(session: Session) -> None:
    existing = {
        (row.entity_type, row.entity_id)
        for row in session.execute(select(EventLog.entity_type, EventLog.entity_id)).all()
    }
    candidates: list[tuple[datetime, str, str]] = []
    for job in session.execute(select(Job)).scalars():
        candidates.append((_to_utc(job.created_at), "job", job.id))
    for audit in session.execute(select(AuditEvent)).scalars():
        candidates.append((_to_utc(audit.created_at), "audit", audit.id))
    for usage in session.execute(select(UsageLedger)).scalars():
        candidates.append((_to_utc(usage.created_at), "usage", usage.id))

    created = False
    for created_at, entity_type, entity_id in sorted(candidates, key=lambda item: (item[0], item[1], item[2])):
        if (entity_type, entity_id) in existing:
            continue
        session.add(EventLog(entity_type=entity_type, entity_id=entity_id, created_at=created_at))
        existing.add((entity_type, entity_id))
        created = True
    if created:
        session.commit()


def _event_payload(session: Session, row: EventLog) -> dict[str, object] | None:
    if row.entity_type == "job":
        job = session.get(Job, row.entity_id)
        if job is None:
            return None
        return {
            "sequenceId": row.sequence_id,
            "type": "job",
            "jobId": job.id,
            "status": job.status,
            "current": job.progress_current,
            "total": job.progress_total,
            "errorCode": job.error_code,
        }
    if row.entity_type == "audit":
        audit = session.get(AuditEvent, row.entity_id)
        if audit is None:
            return None
        return {
            "sequenceId": row.sequence_id,
            "type": "audit",
            "auditId": audit.id,
            "actor": scrub_text(audit.actor),
            "action": scrub_text(audit.action),
            "entityType": scrub_text(audit.entity_type),
            "entityId": audit.entity_id,
        }
    if row.entity_type == "usage":
        usage = session.get(UsageLedger, row.entity_id)
        if usage is None:
            return None
        return {
            "sequenceId": row.sequence_id,
            "type": "usage",
            "usageId": usage.id,
            "provider": scrub_text(usage.provider),
            "model": scrub_text(usage.model),
            "operationId": scrub_text(usage.operation_id),
            "unit": usage.unit,
            "measuredUnits": usage.measured_units,
            "actualVnd": usage.actual_vnd,
        }
    return None


def _to_utc(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    return datetime.fromisoformat(str(value)).astimezone(UTC)


def _parse_cursor(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
