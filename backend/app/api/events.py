from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
import json

from fastapi import APIRouter, Header, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from app.db.base import create_engine_for, session_factory
from app.db.models import AuditEvent, EventLog, Job, UsageLedger
from app.modules.diagnostics.logging import scrub_text
from app.settings.config import Settings


# Source tables the feed projects, in backfill order. The keys match the strings
# stored in event_log.entity_type.
_SOURCE_MODELS: dict[str, type[Job] | type[AuditEvent] | type[UsageLedger]] = {
    "job": Job,
    "audit": AuditEvent,
    "usage": UsageLedger,
}


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
    """Replay feed events with sequenceId > after in ascending sequence order.

    The cursor is filtered in SQL (WHERE sequence_id > :after, served by the
    INTEGER PRIMARY KEY rowid index) and payloads are loaded once per entity type
    instead of once per event, so a replay costs a constant number of statements
    however many events it returns. Passing after=None returns the whole feed.
    """
    _sync_event_log(session)
    statement = select(EventLog).order_by(EventLog.sequence_id.asc())
    if after is not None:
        statement = statement.where(EventLog.sequence_id > after)
    rows = session.execute(statement).scalars().all()
    return _payloads(session, rows, after=after)


def _sync_event_log(session: Session) -> None:
    """Backfill feed rows for source rows that have none, committing only if any were added.

    The missing set is computed in SQL with an anti-join against event_log (probed
    through ix_event_log_entity_sequence), so a replay that finds nothing new loads
    no source rows into Python and writes nothing. The anti-join is exact rather
    than a heuristic count: every source row without a feed row is found, including
    rows that reappear after purge_events_before removed their feed row. The
    backfill and concurrency semantics are unchanged from the previous full-scan
    version.
    """
    candidates = _unsynced_candidates(session)
    if not candidates:
        return

    created = False
    for created_at, entity_type, entity_id in sorted(
        candidates, key=lambda item: (item[0], item[1], item[2])
    ):
        event = EventLog(entity_type=entity_type, entity_id=entity_id, created_at=created_at)
        session.add(event)
        # EventLog is append-only, so the old uniqueness constraint cannot
        # arbitrate concurrent backfill. Drop our pending duplicate when a
        # stream inserted the same entity between the initial read and add.
        with session.no_autoflush:
            raced = session.scalar(
                select(EventLog.sequence_id)
                .where(EventLog.entity_type == entity_type, EventLog.entity_id == entity_id)
                .limit(1)
            )
        if raced is not None:
            session.expunge(event)
            continue
        created = True
    if created:
        try:
            session.commit()
        except IntegrityError:
            session.rollback()


def _unsynced_candidates(session: Session) -> list[tuple[datetime, str, str]]:
    """Source rows (jobs/audit/usage) that have no event_log row yet."""

    candidates: list[tuple[datetime, str, str]] = []
    for entity_type, model in _SOURCE_MODELS.items():
        already_logged = (
            select(EventLog.sequence_id)
            .where(EventLog.entity_type == entity_type, EventLog.entity_id == model.id)
            .exists()
        )
        rows = session.execute(select(model.id, model.created_at).where(~already_logged))
        for entity_id, created_at in rows:
            candidates.append((_to_utc(created_at), entity_type, entity_id))
    return candidates


def _payloads(
    session: Session, rows: list[EventLog], *, after: int | None
) -> list[dict[str, object]]:
    """Build payloads for rows (already in sequence order) with one load per entity type."""

    entities: dict[tuple[str, str], object] = {}
    for entity_type in dict.fromkeys(row.entity_type for row in rows):
        if entity_type not in _SOURCE_MODELS:
            continue
        for entity in _load_entities(session, entity_type, after=after):
            entities[(entity_type, entity.id)] = entity

    events: list[dict[str, object]] = []
    for row in rows:
        entity = entities.get((row.entity_type, row.entity_id))
        if entity is None:
            # Source row is gone: the projection drops the event, as before.
            continue
        payload = _event_payload(row, entity)
        if payload is not None:
            events.append(payload)
    return events


def _load_entities(session: Session, entity_type: str, *, after: int | None) -> Iterator[object]:
    """Load every source row referenced by events of one type in ONE statement.

    The identifier set comes from a subquery over the same cursor slice, so no
    per-row lookup (and no bind-variable limit) is involved.
    """

    model = _SOURCE_MODELS[entity_type]
    slice_ids = select(EventLog.entity_id).where(EventLog.entity_type == entity_type)
    if after is not None:
        slice_ids = slice_ids.where(EventLog.sequence_id > after)
    yield from session.execute(select(model).where(model.id.in_(slice_ids))).scalars()


def _event_payload(row: EventLog, entity: object) -> dict[str, object] | None:
    if row.entity_type == "job" and isinstance(entity, Job):
        return {
            "sequenceId": row.sequence_id,
            "type": "job",
            "jobId": entity.id,
            "status": entity.status,
            "current": entity.progress_current,
            "total": entity.progress_total,
            "errorCode": entity.error_code,
        }
    if row.entity_type == "audit" and isinstance(entity, AuditEvent):
        return {
            "sequenceId": row.sequence_id,
            "type": "audit",
            "auditId": entity.id,
            "actor": scrub_text(entity.actor),
            "action": scrub_text(entity.action),
            "entityType": scrub_text(entity.entity_type),
            "entityId": entity.entity_id,
        }
    if row.entity_type == "usage" and isinstance(entity, UsageLedger):
        return {
            "sequenceId": row.sequence_id,
            "type": "usage",
            "usageId": entity.id,
            "provider": scrub_text(entity.provider),
            "model": scrub_text(entity.model),
            "operationId": scrub_text(entity.operation_id),
            "unit": entity.unit,
            "measuredUnits": entity.measured_units,
            "actualVnd": entity.actual_vnd,
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
