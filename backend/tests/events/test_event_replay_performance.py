from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, event, func, insert, select
from sqlalchemy.orm import Session

from app.api.events import _events, _sync_event_log
from app.contracts import JobKind, JobStatus, RightsStatus, SourceType
from app.db.base import session_factory
from app.db.models import EventLog, Job, Project
from app.modules.events.retention import purge_events_before


BASE_TIME = datetime(2026, 8, 19, 6, 0, tzinfo=UTC)
PROJECT_ID = "018f0000-0000-7000-8000-000000005401"


class _StatementRecorder:
    """Ghi lại text của mọi câu SQL chạy trên engine trong lúc đo."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def __call__(
        self,
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        self.statements.append(statement)


def _seed_project(session: Session) -> None:
    session.add(
        Project(
            id=PROJECT_ID,
            title="Events perf",
            slug="events-perf",
            source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
            rights_status=RightsStatus.PRIVATE_ONLY.value,
        )
    )
    session.flush()


def _job_id(index: int) -> str:
    return f"018f0000-0000-7000-8000-{540000000000 + index:012d}"


def _job_rows(count: int, *, start: int = 0) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(start, start + count):
        created = BASE_TIME + timedelta(seconds=index)
        rows.append(
            {
                "id": _job_id(index),
                "kind": JobKind.TRANSLATE.value,
                "status": JobStatus.QUEUED.value,
                "project_id": PROJECT_ID,
                "chapter_id": None,
                "idempotency_key": f"events-perf-{index}",
                "priority": 100,
                "progress_current": index % 4,
                "progress_total": 4,
                "created_at": created,
                "updated_at": created,
            }
        )
    return rows


def _seed_jobs(engine: Engine, *, count: int, start: int = 0) -> None:
    with engine.begin() as connection:
        connection.execute(insert(Job), _job_rows(count, start=start))


def _measure(
    session: Session, engine: Engine, *, after: int | None
) -> tuple[list[dict[str, object]], list[str]]:
    recorder = _StatementRecorder()
    event.listen(engine, "before_cursor_execute", recorder)
    try:
        events = _events(session, after=after)
    finally:
        event.remove(engine, "before_cursor_execute", recorder)
    return events, recorder.statements


def _feed_count(session: Session) -> int:
    return int(session.scalar(select(func.count(EventLog.sequence_id))) or 0)


def test_replay_after_cursor_returns_only_newer_events_in_sequence_order(
    migrated_engine: Engine, db_session: Session
) -> None:
    _seed_project(db_session)
    db_session.commit()
    _seed_jobs(migrated_engine, count=50)

    full = _events(db_session, after=None)
    assert len(full) == 50

    cursor = 20
    replay = _events(db_session, after=cursor)

    assert [item["sequenceId"] for item in replay] == list(range(cursor + 1, 51))
    assert [item["sequenceId"] for item in replay] == sorted(item["sequenceId"] for item in replay)
    assert len({item["sequenceId"] for item in replay}) == len(replay)
    # Parity: lọc bằng SQL cho đúng kết quả của lọc bằng Python trên toàn bộ feed.
    assert replay == [item for item in full if int(item["sequenceId"]) > cursor]


def test_replay_filters_cursor_in_sql_not_in_python(
    migrated_engine: Engine, db_session: Session
) -> None:
    _seed_project(db_session)
    db_session.commit()
    _seed_jobs(migrated_engine, count=30)
    _events(db_session, after=None)

    _, statements = _measure(db_session, migrated_engine, after=25)

    feed_reads = [sql for sql in statements if "FROM event_log" in sql]
    assert feed_reads, statements
    assert any("event_log.sequence_id >" in sql for sql in feed_reads), feed_reads


def test_replay_statement_count_does_not_grow_with_event_count(
    migrated_engine: Engine, db_session: Session
) -> None:
    _seed_project(db_session)
    db_session.commit()
    _seed_jobs(migrated_engine, count=1000)
    # Prime the projection: the measured replay is the steady state.
    assert len(_events(db_session, after=None)) == 1000

    replayed_all, wide = _measure(db_session, migrated_engine, after=0)
    replayed_one, narrow = _measure(db_session, migrated_engine, after=999)

    assert len(replayed_all) == 1000
    assert len(replayed_one) == 1
    # 1k event => một lượng câu SQL hằng số, không tăng theo số event.
    assert len(wide) <= 10, wide
    assert len(narrow) >= 1
    assert len(wide) == len(narrow), (wide, narrow)
    # Không còn N+1: không có câu nào đọc nguồn theo từng dòng (session.get).
    assert not [sql for sql in wide if "jobs.id = ?" in sql], wide


def test_second_replay_of_the_same_slice_changes_nothing(
    migrated_engine: Engine, db_session: Session
) -> None:
    _seed_project(db_session)
    db_session.commit()
    _seed_jobs(migrated_engine, count=10)
    _events(db_session, after=None)

    _first, statements = _measure(db_session, migrated_engine, after=0)

    assert not [sql for sql in statements if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert _feed_count(db_session) == 10


def test_sync_event_log_is_idempotent_and_detects_new_source_rows(
    migrated_engine: Engine, db_session: Session
) -> None:
    _seed_project(db_session)
    db_session.commit()
    _seed_jobs(migrated_engine, count=3)

    first = _events(db_session, after=None)
    assert [item["jobId"] for item in first] == [_job_id(0), _job_id(1), _job_id(2)]
    assert _feed_count(db_session) == 3

    _sync_event_log(db_session)

    assert _feed_count(db_session) == 3

    # Nhưng nguồn MỚI sau đó vẫn phải được phát hiện.
    _seed_jobs(migrated_engine, count=1, start=3)
    incremental = _events(db_session, after=first[-1]["sequenceId"])

    assert len(incremental) == 1
    assert incremental[0]["jobId"] == _job_id(3)
    assert _feed_count(db_session) == 4


def test_sync_event_log_restores_feed_rows_removed_by_purge(
    migrated_engine: Engine, db_session: Session
) -> None:
    _seed_project(db_session)
    db_session.commit()
    _seed_jobs(migrated_engine, count=4)

    original = _events(db_session, after=None)
    assert len(original) == 4
    removed = purge_events_before(db_session, BASE_TIME + timedelta(seconds=2))
    assert removed == 2

    restored = _events(db_session, after=None)

    assert sorted(item["jobId"] for item in restored) == sorted(item["jobId"] for item in original)
    assert len({item["sequenceId"] for item in restored}) == 4
    assert _feed_count(db_session) == 4


def test_backfill_still_drops_its_row_when_a_concurrent_stream_wins(
    migrated_engine: Engine, db_session: Session, monkeypatch: Any
) -> None:
    _seed_project(db_session)
    db_session.commit()
    _seed_jobs(migrated_engine, count=1)

    original_add = db_session.add
    raced = False

    def add_after_concurrent_insert(instance: object, *args: object, **kwargs: object) -> None:
        nonlocal raced
        if isinstance(instance, EventLog) and not raced:
            raced = True
            factory = session_factory(db_session.get_bind())
            with factory() as concurrent:
                concurrent.add(
                    EventLog(
                        entity_type=instance.entity_type,
                        entity_id=instance.entity_id,
                        created_at=instance.created_at,
                    )
                )
                concurrent.commit()
        original_add(instance, *args, **kwargs)

    monkeypatch.setattr(db_session, "add", add_after_concurrent_insert)

    events = _events(db_session, after=None)

    assert raced
    assert _feed_count(db_session) == 1
    assert [item["jobId"] for item in events] == [_job_id(0)]

def test_replay_cursor_filter_uses_the_primary_key_index(
    migrated_engine: Engine, db_session: Session
) -> None:
    from sqlalchemy import text

    _seed_project(db_session)
    db_session.commit()
    _seed_jobs(migrated_engine, count=25)
    _events(db_session, after=None)

    with migrated_engine.connect() as connection:
        plan = [
            str(row[3])
            for row in connection.execute(
                text("EXPLAIN QUERY PLAN SELECT * FROM event_log WHERE sequence_id > 5 ORDER BY sequence_id")
            )
        ]

    assert plan, "SQLite phải trả về query plan"
    assert any("INTEGER PRIMARY KEY" in line or "SEARCH event_log" in line for line in plan), plan
    assert not any("SCAN event_log" in line for line in plan), plan


def test_stale_and_invalid_cursors_keep_the_documented_behaviour(
    migrated_engine: Engine, db_session: Session
) -> None:
    _seed_project(db_session)
    db_session.commit()
    _seed_jobs(migrated_engine, count=5)

    everything = _events(db_session, after=None)
    assert len(everything) == 5

    # Cursor cũ hơn mọi event => trả lại toàn bộ feed (không có 410 trong hợp đồng hiện tại).
    assert _events(db_session, after=0) == everything
    # Cursor mới hơn mọi event => không còn gì để replay.
    assert _events(db_session, after=everything[-1]["sequenceId"]) == []
    # Header không phải số => _parse_cursor trả None => toàn bộ feed (giữ nguyên như trước).
    from app.api.events import _parse_cursor

    assert _parse_cursor("abc") is None
    assert _parse_cursor("3") == 3
