from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import Engine, text

from app.contracts import (
    ChapterState,
    ImportKind,
    JobKind,
    RightsStatus,
    RunStatus,
    SourceType,
)
from app.db.base import session_factory
from app.db.models import Chapter, SourceRevision, SourceSegment, TranslationRun, TranslationSegment
from app.modules.jobs.runner import ErrorRecord, JobRunner
from app.modules.translation.workflow import TranslationWorkflow


NOW = datetime(2026, 8, 19, 6, 0, tzinfo=UTC)


def _event_count(engine: Engine, entity_type: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT COUNT(*) FROM event_log WHERE entity_type = :t"),
                {"t": entity_type},
            ).scalar_one()
        )


def _seed_project(engine: Engine) -> dict[str, str]:
    project_id = "018f0000-0000-7000-8000-000000000501"
    chapter_id = "018f0000-0000-7000-8000-000000000502"
    revision_id = "018f0000-0000-7000-8000-000000000503"
    now = NOW.isoformat()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, title, slug, source_type, rights_status, default_language, "
                "target_language, created_at, updated_at) VALUES (:id, 'T', 'events-emission', :st, :rs, "
                "'zh-CN', 'vi-VN', :now, :now)"
            ),
            {
                "id": project_id,
                "st": SourceType.USER_SUPPLIED_PRIVATE.value,
                "rs": RightsStatus.PRIVATE_ONLY.value,
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO chapters (id, project_id, ordinal, state, created_at, updated_at) "
                "VALUES (:id, :pid, 1, :state, :now, :now)"
            ),
            {"id": chapter_id, "pid": project_id, "state": ChapterState.NORMALIZED.value, "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO source_revisions (id, chapter_id, revision_no, import_kind, normalized_text, "
                "normalized_sha256, han_char_count, total_char_count, normalizer_version, created_at, updated_at) "
                "VALUES (:id, :cid, 1, :ik, :body, :sha, 0, 3, 'nfc-v1', :now, :now)"
            ),
            {
                "id": revision_id,
                "cid": chapter_id,
                "ik": ImportKind.PASTE.value,
                "body": "一",
                "sha": hashlib.sha256("一".encode("utf-8")).hexdigest(),
                "now": now,
            },
        )
        connection.execute(
            text("UPDATE chapters SET active_source_revision_id = :rid WHERE id = :cid"),
            {"rid": revision_id, "cid": chapter_id},
        )
    return {"project_id": project_id, "chapter_id": chapter_id, "revision_id": revision_id}


def test_job_completion_emits_feed_event_in_same_transaction(
    migrated_engine: Engine, deterministic_uuid7_factory
) -> None:
    seed = _seed_project(migrated_engine)
    runner = JobRunner(migrated_engine, id_factory=deterministic_uuid7_factory)
    job = runner.enqueue(JobKind.IMPORT, seed["project_id"], None, "emit-complete")
    lease = runner.claim("worker-a", NOW)
    assert lease is not None

    runner.complete(job.id, None, lease.worker_id, lease.attempt_id, NOW)

    assert _event_count(migrated_engine, "job") == 1


def test_retryable_failure_does_not_emit_event(
    migrated_engine: Engine, deterministic_uuid7_factory
) -> None:
    seed = _seed_project(migrated_engine)
    runner = JobRunner(migrated_engine, id_factory=deterministic_uuid7_factory)
    job = runner.enqueue(JobKind.IMPORT, seed["project_id"], None, "emit-retry")
    lease = runner.claim("worker-a", NOW)
    assert lease is not None

    runner.fail(
        job.id,
        ErrorRecord(code="DB_BUSY", summary="busy", retryable=True),
        lease.worker_id,
        lease.attempt_id,
        now=NOW,
    )

    assert _event_count(migrated_engine, "job") == 0


def test_terminal_failure_emits_event_once(
    migrated_engine: Engine, deterministic_uuid7_factory
) -> None:
    seed = _seed_project(migrated_engine)
    runner = JobRunner(migrated_engine, id_factory=deterministic_uuid7_factory)
    job = runner.enqueue(JobKind.IMPORT, seed["project_id"], None, "emit-fail")
    lease = runner.claim("worker-a", NOW)
    assert lease is not None

    runner.fail(
        job.id,
        ErrorRecord(code="INPUT_UNSUPPORTED_JOB_KIND", summary="no handler", retryable=False),
        lease.worker_id,
        lease.attempt_id,
        now=NOW,
    )

    assert _event_count(migrated_engine, "job") == 1


def test_translation_approval_emits_audit_event_in_same_transaction(
    migrated_engine: Engine, deterministic_uuid7_factory
) -> None:
    seed = _seed_project(migrated_engine)
    session = session_factory(migrated_engine)()
    try:
        revision = session.get(SourceRevision, seed["revision_id"])
        assert revision is not None
        segment = SourceSegment(
            id="018f0000-0000-7000-8000-000000000504",
            source_revision_id=revision.id,
            segment_index=0,
            paragraph_start=0,
            paragraph_end=0,
            source_text="一",
            source_sha256=hashlib.sha256("一".encode("utf-8")).hexdigest(),
            segment_kind="SOURCE",
        )
        session.add(segment)
        session.flush()
        run = TranslationRun(
            id="018f0000-0000-7000-8000-000000000505",
            chapter_id=seed["chapter_id"],
            source_revision_id=revision.id,
            prompt_version="translation-v1",
            status=RunStatus.REVIEW.value,
            translation_text_sha256="a" * 64,
            estimated_cost_vnd=0,
            actual_cost_vnd=0,
        )
        session.add(run)
        session.flush()
        session.add(
            TranslationSegment(
                id="018f0000-0000-7000-8000-000000000506",
                translation_run_id=run.id,
                source_segment_id=segment.id,
                target_text="Bản dịch.",
                target_sha256=hashlib.sha256("Bản dịch.".encode("utf-8")).hexdigest(),
                was_cache_hit=False,
                manually_edited=False,
            )
        )
        session.flush()

        chapter = session.get(Chapter, seed["chapter_id"])
        assert chapter is not None
        chapter.state = ChapterState.TRANSLATION_REVIEW.value
        session.flush()

        TranslationWorkflow(session, id_factory=deterministic_uuid7_factory).approve_revision(
            seed["chapter_id"], run.id, "a" * 64
        )
    finally:
        session.close()

    assert _event_count(migrated_engine, "audit") == 1


def test_projection_rebuild_after_emission_adds_no_duplicates(
    migrated_engine: Engine, deterministic_uuid7_factory
) -> None:
    from app.modules.events.retention import rebuild_feed

    seed = _seed_project(migrated_engine)
    runner = JobRunner(migrated_engine, id_factory=deterministic_uuid7_factory)
    job = runner.enqueue(JobKind.IMPORT, seed["project_id"], None, "emit-rebuild")
    lease = runner.claim("worker-a", NOW)
    assert lease is not None
    runner.complete(job.id, None, lease.worker_id, lease.attempt_id, NOW)

    session = session_factory(migrated_engine)()
    try:
        added = rebuild_feed(session)
    finally:
        session.close()

    assert added == 0
    assert _event_count(migrated_engine, "job") == 1
