from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.contracts import JobKind, JobStatus, RightsStatus, SourceType
from app.db.models import EventLog, Job, Project
from app.modules.events.retention import purge_events_before, rebuild_feed


def _seed(db_session) -> None:
    project = Project(
        id="018f0000-0000-7000-8000-000000000401",
        title="T",
        slug="events-retention",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
    )
    db_session.add(project)
    db_session.flush()
    for index, created_at in enumerate(
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC))
    ):
        db_session.add(
            Job(
                id=f"018f0000-0000-7000-8000-00000000041{index}",
                kind=JobKind.IMPORT.value,
                status=JobStatus.QUEUED.value,
                project_id=project.id,
                chapter_id=None,
                idempotency_key=f"k-{index}",
                priority=100,
                progress_current=0,
                progress_total=0,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    db_session.commit()


def _feed_count(db_session) -> int:
    return int(db_session.scalar(select(func.count(EventLog.sequence_id))) or 0)


def test_rebuild_feed_is_idempotent(db_session) -> None:
    _seed(db_session)

    assert rebuild_feed(db_session) == 2
    assert rebuild_feed(db_session) == 0
    assert _feed_count(db_session) == 2


def test_purge_removes_only_rows_before_explicit_cutoff(db_session) -> None:
    _seed(db_session)
    rebuild_feed(db_session)

    removed = purge_events_before(db_session, datetime(2026, 3, 1, tzinfo=UTC))

    assert removed == 1
    assert _feed_count(db_session) == 1


def test_rebuild_restores_projection_after_purge(db_session) -> None:
    _seed(db_session)
    rebuild_feed(db_session)
    purge_events_before(db_session, datetime(2026, 3, 1, tzinfo=UTC))

    restored = rebuild_feed(db_session)

    assert restored == 1
    assert _feed_count(db_session) == 2
