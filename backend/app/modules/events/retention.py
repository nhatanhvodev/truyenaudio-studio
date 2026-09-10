"""Event-feed retention and rebuild helpers (task J03).

Feed rows are append-only references; payload is synthesized at read time.
``purge_events_before`` removes feed rows older than an explicit cutoff the
caller supplies (no retention window is invented here). ``rebuild_feed``
re-synchronizes the feed from the source tables (jobs/audit/usage) the same
way ``/api/events`` backfills, so a projection can be rebuilt without
duplicates.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, EventLog, Job, UsageLedger


def purge_events_before(session: Session, cutoff: datetime) -> int:
    """Delete feed rows strictly older than ``cutoff`` (caller-provided policy).

    Returns the number of removed rows. Never deletes source tables — the
    feed is a projection and can be rebuilt from them.
    """
    cutoff = cutoff.astimezone(UTC)
    result = session.execute(delete(EventLog).where(EventLog.created_at < cutoff))
    session.commit()
    return int(result.rowcount or 0)


def rebuild_feed(session: Session) -> int:
    """Re-sync feed rows from source tables, idempotently (no duplicates).

    Mirrors the /api/events backfill semantics: one row per distinct
    (entity_type, entity_id) among jobs/audit/usage, preserving created_at.
    Returns the number of newly added rows.
    """
    existing = {
        (row.entity_type, row.entity_id)
        for row in session.execute(select(EventLog.entity_type, EventLog.entity_id)).all()
    }
    candidates: list[tuple[str, str, str]] = []
    for row in session.execute(select(Job.id, Job.created_at)):
        candidates.append((str(row.created_at), "job", row.id))
    for row in session.execute(select(AuditEvent.id, AuditEvent.created_at)):
        candidates.append((str(row.created_at), "audit", row.id))
    for row in session.execute(select(UsageLedger.id, UsageLedger.created_at)):
        candidates.append((str(row.created_at), "usage", row.id))

    added = 0
    for created_at, entity_type, entity_id in sorted(candidates, key=lambda item: (item[0], item[1], item[2])):
        if (entity_type, entity_id) in existing:
            continue
        session.add(
            EventLog(
                entity_type=entity_type,
                entity_id=entity_id,
                created_at=_parse(created_at),
            )
        )
        existing.add((entity_type, entity_id))
        added += 1
    if added:
        session.commit()
    return added


def _parse(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)).astimezone(UTC)
