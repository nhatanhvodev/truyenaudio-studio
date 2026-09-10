"""REVIEW and SUMMARIZE worker handlers (task J01, final slice).

Every test runs offline against a real alembic-migrated SQLite database and the
real JobRunner/Worker: the conftest no_network fixture rejects any non-loopback
socket, so a handler that reached a provider would fail here instead of silently
passing. Cloud reviewer/summarizer dispatch is NOT_RUN in this round (no
credential, consent or budget): the tests assert the fail-closed codes instead.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import Engine, text

from app.contracts import (
    JobKind,
    JobStatus,
    ProviderKind,
    QaCategory,
    QaSeverity,
    QaStatus,
    RightsStatus,
    SourceType,
)
from app.db.base import session_factory
from app.modules.jobs.execution_handlers import (
    build_review_handler,
    build_summarize_handler,
)
from app.modules.jobs.recovery import RecoveryCanceled, RecoveryJobContext
from app.modules.jobs.runner import JobRunner
from app.settings.config import Settings
from app.worker import (
    HandlerUnavailable,
    Worker,
    _unconfigured_recovery_handler,
    build_default_handlers,
)


NOW = datetime(2026, 8, 19, 3, 0, 0, tzinfo=UTC)
PROJECT_ID = "018f0000-0000-7000-8000-000000007001"
CHAPTER_ID = "018f0000-0000-7000-8000-000000007002"
REVISION_ID = "018f0000-0000-7000-8000-000000007003"
RUN_ID = "018f0000-0000-7000-8000-000000007004"
FAKE_PROFILE_ID = "018f0000-0000-7000-8000-000000007005"
QWEN_PROFILE_ID = "018f0000-0000-7000-8000-000000007006"
LOCAL_PROFILE_ID = "018f0000-0000-7000-8000-000000007007"
OTHER_CHAPTER_ID = "018f0000-0000-7000-8000-000000007008"
OTHER_REVISION_ID = "018f0000-0000-7000-8000-000000007009"
MISSING_CHAPTER_ID = "018f0000-0000-7000-8000-0000000070ff"
OTHER_SOURCE_TEXT = "山村清晨，少年背着行李走出家门。"
RESIDUAL_HAN_RULE = "deterministic-qa-v1:residual-han"
EMPTY_TARGET_RULE = "deterministic-qa-v1:empty-target"
# The local recap is extracted from the newest translation run of the chapter.
EXPECTED_LOCAL_SUMMARY = "第一 Thiếu niên rời làng."
SEGMENTS = (
    ("018f0000-0000-7000-8000-000000007101", "第一章", "第一"),
    ("018f0000-0000-7000-8000-000000007102", "少年走出山村。", "Thiếu niên rời làng."),
    ("018f0000-0000-7000-8000-000000007103", "他出发了。", ""),
)


@pytest.fixture
def worker_db_path(migrated_engine: Engine) -> Path:
    return Path(migrated_engine.url.database)


@pytest.fixture
def seeded(migrated_engine: Engine) -> dict[str, str]:
    return _seed(migrated_engine)


def _seed(engine: Engine) -> dict[str, str]:
    now = NOW.isoformat()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, title, slug, source_type, rights_status, default_language, "
                "target_language, created_at, updated_at) VALUES (:id, 'T', 'review-summarize', :st, "
                ":rs, 'zh-CN', 'vi-VN', :now, :now)"
            ),
            {
                "id": PROJECT_ID,
                "st": SourceType.USER_SUPPLIED_PRIVATE.value,
                "rs": RightsStatus.PRIVATE_ONLY.value,
                "now": now,
            },
        )
        for provider_kind, profile_id, adapter_name, enabled in (
            (ProviderKind.TRANSLATOR, FAKE_PROFILE_ID, "fake-hanviet-v2", 1),
            (ProviderKind.REVIEWER, QWEN_PROFILE_ID, "qwen-mt", 1),
            (ProviderKind.REVIEWER, LOCAL_PROFILE_ID, "local-hanviet", 1),
        ):
            connection.execute(
                text(
                    "INSERT INTO provider_profiles (id, provider_kind, adapter_name, display_name, model, "
                    "region, enabled, created_at, updated_at) VALUES (:id, :kind, :adapter, :adapter, "
                    ":adapter, 'local', :enabled, :now, :now)"
                ),
                {
                    "id": profile_id,
                    "kind": provider_kind.value,
                    "adapter": adapter_name,
                    "enabled": enabled,
                    "now": now,
                },
            )
        for chapter_id, ordinal in ((CHAPTER_ID, 1), (OTHER_CHAPTER_ID, 2)):
            connection.execute(
                text(
                    "INSERT INTO chapters (id, project_id, ordinal, state, created_at, updated_at) "
                    "VALUES (:id, :pid, :ordinal, 'TRANSLATION_REVIEW', :now, :now)"
                ),
                {"id": chapter_id, "pid": PROJECT_ID, "ordinal": ordinal, "now": now},
            )
        connection.execute(
            text(
                "INSERT INTO source_revisions (id, chapter_id, revision_no, import_kind, normalized_text, "
                "normalized_sha256, han_char_count, total_char_count, normalizer_version, created_at, "
                "updated_at) VALUES (:id, :cid, 1, :ik, :body, :sha, 0, :size, 'nfc-v1', :now, :now)"
            ),
            {
                "id": REVISION_ID,
                "cid": CHAPTER_ID,
                "ik": "PASTE",
                "body": "\n".join(segment[1] for segment in SEGMENTS),
                "sha": hashlib.sha256("".join(segment[1] for segment in SEGMENTS).encode("utf-8")).hexdigest(),
                "size": sum(len(segment[1]) for segment in SEGMENTS),
                "now": now,
            },
        )
        connection.execute(
            text("UPDATE chapters SET active_source_revision_id = :rid WHERE id = :cid"),
            {"rid": REVISION_ID, "cid": CHAPTER_ID},
        )
        connection.execute(
            text(
                "INSERT INTO source_revisions (id, chapter_id, revision_no, import_kind, normalized_text, "
                "normalized_sha256, han_char_count, total_char_count, normalizer_version, created_at, "
                "updated_at) VALUES (:id, :cid, 1, :ik, :body, :sha, 0, :size, 'nfc-v1', :now, :now)"
            ),
            {
                "id": OTHER_REVISION_ID,
                "cid": OTHER_CHAPTER_ID,
                "ik": "PASTE",
                "body": OTHER_SOURCE_TEXT,
                "sha": hashlib.sha256(OTHER_SOURCE_TEXT.encode("utf-8")).hexdigest(),
                "size": len(OTHER_SOURCE_TEXT),
                "now": now,
            },
        )
        connection.execute(
            text("UPDATE chapters SET active_source_revision_id = :rid WHERE id = :cid"),
            {"rid": OTHER_REVISION_ID, "cid": OTHER_CHAPTER_ID},
        )
        connection.execute(
            text(
                "INSERT INTO translation_runs (id, chapter_id, source_revision_id, provider_profile_id, "
                "model, prompt_version, status, estimated_cost_vnd, actual_cost_vnd, created_by, "
                "created_at, updated_at) VALUES (:id, :cid, :rid, :pid, 'fake-hanviet-v2', "
                "'prompt-builder.v1', 'REVIEW', 0, 0, 'LOCAL_OWNER', :now, :now)"
            ),
            {
                "id": RUN_ID,
                "cid": CHAPTER_ID,
                "rid": REVISION_ID,
                "pid": FAKE_PROFILE_ID,
                "now": now,
            },
        )
        for index, (segment_id, source_text, target_text) in enumerate(SEGMENTS):
            connection.execute(
                text(
                    "INSERT INTO source_segments (id, source_revision_id, segment_index, paragraph_start, "
                    "paragraph_end, source_text, source_sha256, segment_kind, created_at, updated_at) "
                    "VALUES (:id, :rid, :idx, :idx, :idx, :body, :sha, 'SOURCE', :now, :now)"
                ),
                {
                    "id": segment_id,
                    "rid": REVISION_ID,
                    "idx": index,
                    "body": source_text,
                    "sha": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                    "now": now,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO translation_segments (id, translation_run_id, source_segment_id, "
                    "target_text, target_sha256, was_cache_hit, manually_edited, created_at, updated_at) "
                    "VALUES (:id, :run, :segment, :target, :sha, 0, 0, :now, :now)"
                ),
                {
                    "id": f"018f0000-0000-7000-8000-0000000072{index:02d}",
                    "run": RUN_ID,
                    "segment": segment_id,
                    "target": target_text,
                    "sha": hashlib.sha256(target_text.encode("utf-8")).hexdigest(),
                    "now": now,
                },
            )
    return {"project_id": PROJECT_ID, "chapter_id": CHAPTER_ID, "run_id": RUN_ID}


def _plan(chapter_id: str, profile_id: str, revision_id: str) -> dict[str, str]:
    return {
        "projectId": PROJECT_ID,
        "chapterId": chapter_id,
        "profileId": profile_id,
        "cloudConsentId": "consent-1",
        "budgetAuthorizationId": "auth-1",
        "revisionId": revision_id,
    }


def _worker(
    migrated_engine: Engine,
    db_path: Path,
    tmp_path: Path,
    id_factory,
) -> tuple[JobRunner, Worker]:
    settings = Settings(data_root=tmp_path)
    runner = JobRunner(migrated_engine, id_factory=id_factory)
    handlers = {
        JobKind.REVIEW: build_review_handler(settings, db_path=db_path),
        JobKind.SUMMARIZE: build_summarize_handler(settings, db_path=db_path),
    }
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(exist_ok=True)
    return runner, Worker(
        runner,
        handlers=handlers,
        artifact_root=artifact_root,
        clock=lambda: NOW,
    )


def _scalar(engine: Engine, sql: str, **params: object) -> object:
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _rows(engine: Engine, sql: str, **params: object) -> list[dict[str, object]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(sql), params).mappings().all()]


def _qa_count(engine: Engine) -> int:
    return int(_scalar(engine, "SELECT COUNT(*) FROM qa_issues"))


def _memory_rows(engine: Engine) -> list[dict[str, object]]:
    return _rows(
        engine,
        "SELECT entity_key, entity_type, summary, status, revision_no, valid_from_ordinal, "
        "source_run_id FROM story_memory_entries ORDER BY revision_no",
    )


class _StubRunner:
    """Minimal runner for a handler call that must not touch the real queue."""

    def __init__(self, job: object) -> None:
        self._job = job

    def get(self, job_id: str) -> object:
        return self._job


def test_default_handlers_register_translate_synthesize_review_summarize(tmp_path: Path) -> None:
    handlers = build_default_handlers(Settings(data_root=tmp_path))

    configured = {
        JobKind.TRANSLATE,
        JobKind.SYNTHESIZE,
        JobKind.REVIEW,
        JobKind.SUMMARIZE,
    }
    assert configured <= set(handlers)
    for kind in configured:
        assert tuple(handlers[kind].__code__.co_varnames[:2]) == ("lease", "recovery")
    for kind in set(JobKind) - configured:
        assert handlers[kind] is _unconfigured_recovery_handler


@pytest.mark.asyncio
async def test_review_handler_runs_fake_profile_offline_and_records_issues(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    deterministic_uuid7_factory,
    seeded: dict[str, str],
) -> None:
    assert _qa_count(migrated_engine) == 0
    runner, worker = _worker(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)
    job = runner.enqueue(
        JobKind.REVIEW,
        PROJECT_ID,
        CHAPTER_ID,
        "review-fake",
        plan=_plan(CHAPTER_ID, FAKE_PROFILE_ID, REVISION_ID),
    )

    await worker.run_once()

    assert runner.get(job.id).status is JobStatus.SUCCEEDED
    rows = _rows(migrated_engine, "SELECT * FROM qa_issues")
    assert len(rows) == 2
    by_rule = {row["rule_or_model"]: row for row in rows}
    assert set(by_rule) == {RESIDUAL_HAN_RULE, EMPTY_TARGET_RULE}
    residual = by_rule[RESIDUAL_HAN_RULE]
    assert residual["category"] == QaCategory.RESIDUAL_HAN.value
    assert residual["severity"] == QaSeverity.MAJOR.value
    assert residual["status"] == QaStatus.OPEN.value
    assert residual["translation_run_id"] == RUN_ID
    assert residual["source_segment_id"] == SEGMENTS[0][0]
    assert residual["evidence"] == "第"
    empty = by_rule[EMPTY_TARGET_RULE]
    assert empty["severity"] == QaSeverity.CRITICAL.value
    assert empty["source_segment_id"] == SEGMENTS[2][0]


@pytest.mark.asyncio
async def test_review_handler_without_plan_fails_closed_without_writing(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    deterministic_uuid7_factory,
    seeded: dict[str, str],
) -> None:
    runner, worker = _worker(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)
    job = runner.enqueue(JobKind.REVIEW, PROJECT_ID, CHAPTER_ID, "review-noplan")

    await worker.run_once()

    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.error_code == "REVIEW_PLAN_REQUIRED"
    assert _qa_count(migrated_engine) == 0


@pytest.mark.asyncio
async def test_review_handler_refuses_stale_plan_revision_without_writing(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    deterministic_uuid7_factory,
    seeded: dict[str, str],
) -> None:
    runner, worker = _worker(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)
    job = runner.enqueue(
        JobKind.REVIEW,
        PROJECT_ID,
        CHAPTER_ID,
        "review-stale",
        plan=_plan(CHAPTER_ID, FAKE_PROFILE_ID, "0" * 36),
    )

    await worker.run_once()

    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.error_code == "REVIEW_REVISION_STALE"
    assert _qa_count(migrated_engine) == 0


@pytest.mark.asyncio
async def test_review_handler_refuses_unknown_chapter_without_writing(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    seeded: dict[str, str],
) -> None:
    # jobs.chapter_id is a foreign key, so the queue cannot hold a dangling
    # chapter: the guard covers the delete-after-enqueue race and is exercised
    # here with a synthetic lease that names a chapter that does not exist.
    handler = build_review_handler(Settings(data_root=tmp_path), db_path=worker_db_path)
    job = SimpleNamespace(
        plan=_plan(MISSING_CHAPTER_ID, FAKE_PROFILE_ID, REVISION_ID),
        chapter_id=MISSING_CHAPTER_ID,
        status=JobStatus.RUNNING,
    )
    lease = SimpleNamespace(job_id="018f0000-0000-7000-8000-0000000070ee")

    with session_factory(migrated_engine)() as session:
        recovery = RecoveryJobContext(
            runner=_StubRunner(job),
            lease=lease,
            session=session,
            artifact_root=tmp_path / "artifacts",
        )
        with pytest.raises(HandlerUnavailable) as failure:
            await handler(lease, recovery)

    assert failure.value.code == "REVIEW_CHAPTER_NOT_FOUND"
    assert _qa_count(migrated_engine) == 0


@pytest.mark.asyncio
async def test_review_handler_refuses_cloud_profile_without_writing(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    deterministic_uuid7_factory,
    seeded: dict[str, str],
) -> None:
    runner, worker = _worker(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)
    job = runner.enqueue(
        JobKind.REVIEW,
        PROJECT_ID,
        CHAPTER_ID,
        "review-cloud",
        plan=_plan(CHAPTER_ID, QWEN_PROFILE_ID, REVISION_ID),
    )

    await worker.run_once()

    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.error_code == "REVIEW_CLOUD_PROVIDER_NOT_WIRED"
    assert _qa_count(migrated_engine) == 0


@pytest.mark.asyncio
async def test_review_handler_refuses_unsupported_adapter_without_writing(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    deterministic_uuid7_factory,
    seeded: dict[str, str],
) -> None:
    runner, worker = _worker(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)
    job = runner.enqueue(
        JobKind.REVIEW,
        PROJECT_ID,
        CHAPTER_ID,
        "review-local",
        plan=_plan(CHAPTER_ID, LOCAL_PROFILE_ID, REVISION_ID),
    )

    await worker.run_once()

    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.error_code == "REVIEW_PROVIDER_UNSUPPORTED:local-hanviet"
    assert _qa_count(migrated_engine) == 0


@pytest.mark.asyncio
async def test_review_handler_is_idempotent_across_repeated_jobs(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    deterministic_uuid7_factory,
    seeded: dict[str, str],
) -> None:
    runner, worker = _worker(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)
    first = runner.enqueue(
        JobKind.REVIEW,
        PROJECT_ID,
        CHAPTER_ID,
        "review-first",
        plan=_plan(CHAPTER_ID, FAKE_PROFILE_ID, REVISION_ID),
    )
    second = runner.enqueue(
        JobKind.REVIEW,
        PROJECT_ID,
        CHAPTER_ID,
        "review-second",
        plan=_plan(CHAPTER_ID, FAKE_PROFILE_ID, REVISION_ID),
    )

    await worker.run_once()
    after_first = _qa_count(migrated_engine)
    await worker.run_once()

    assert runner.get(first.id).status is JobStatus.SUCCEEDED
    assert runner.get(second.id).status is JobStatus.SUCCEEDED
    assert after_first == 2
    assert _qa_count(migrated_engine) == 2


@pytest.mark.asyncio
async def test_review_handler_does_not_wrap_recovery_canceled(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    deterministic_uuid7_factory,
    seeded: dict[str, str],
) -> None:
    runner = JobRunner(migrated_engine, id_factory=deterministic_uuid7_factory)
    handler = build_review_handler(Settings(data_root=tmp_path), db_path=worker_db_path)
    job = runner.enqueue(
        JobKind.REVIEW,
        PROJECT_ID,
        CHAPTER_ID,
        "review-cancel",
        plan=_plan(CHAPTER_ID, FAKE_PROFILE_ID, REVISION_ID),
    )
    lease = runner.claim("worker-a", NOW)
    assert lease is not None
    runner.request_cancel(job.id, NOW)

    with session_factory(migrated_engine)() as session:
        recovery = RecoveryJobContext(
            runner=runner,
            lease=lease,
            session=session,
            artifact_root=tmp_path / "artifacts",
        )
        # RecoveryCanceled must escape unwrapped: the worker, not the handler,
        # owns the cancel acknowledgement.
        with pytest.raises(RecoveryCanceled):
            await handler(lease, recovery)

    assert runner.get(job.id).status is JobStatus.CANCEL_REQUESTED
    assert _qa_count(migrated_engine) == 0


@pytest.mark.asyncio
async def test_worker_acknowledges_review_cancel_without_writing(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    deterministic_uuid7_factory,
    seeded: dict[str, str],
) -> None:
    runner, worker = _worker(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)
    review_handler = worker.handlers[JobKind.REVIEW]

    async def cancel_while_running(lease, recovery):
        # A cancel that lands on a RUNNING job becomes CANCEL_REQUESTED, which is
        # the state the worker must acknowledge instead of completing the job.
        runner.request_cancel(lease.job_id, NOW)
        return await review_handler(lease, recovery)

    worker.handlers = {JobKind.REVIEW: cancel_while_running}
    job = runner.enqueue(
        JobKind.REVIEW,
        PROJECT_ID,
        CHAPTER_ID,
        "review-cancel-worker",
        plan=_plan(CHAPTER_ID, FAKE_PROFILE_ID, REVISION_ID),
    )

    assert await worker.run_once() is True

    assert runner.get(job.id).status is JobStatus.CANCELED
    assert _qa_count(migrated_engine) == 0


@pytest.mark.asyncio
async def test_summarize_handler_runs_fake_profile_offline_and_writes_candidate(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    deterministic_uuid7_factory,
    seeded: dict[str, str],
) -> None:
    assert _memory_rows(migrated_engine) == []
    runner, worker = _worker(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)
    job = runner.enqueue(
        JobKind.SUMMARIZE,
        PROJECT_ID,
        CHAPTER_ID,
        "summarize-fake",
        plan=_plan(CHAPTER_ID, FAKE_PROFILE_ID, REVISION_ID),
    )

    await worker.run_once()

    assert runner.get(job.id).status is JobStatus.SUCCEEDED
    rows = _memory_rows(migrated_engine)
    assert len(rows) == 1
    entry = rows[0]
    assert entry["entity_key"] == "chapter:1"
    assert entry["entity_type"] == "CHAPTER"
    assert entry["summary"] == EXPECTED_LOCAL_SUMMARY
    assert entry["status"] == "CANDIDATE"
    assert entry["revision_no"] == 1
    assert entry["valid_from_ordinal"] == 1
    assert entry["source_run_id"] is None


@pytest.mark.asyncio
async def test_summarize_handler_without_plan_fails_closed_without_writing(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    deterministic_uuid7_factory,
    seeded: dict[str, str],
) -> None:
    runner, worker = _worker(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)
    job = runner.enqueue(JobKind.SUMMARIZE, PROJECT_ID, CHAPTER_ID, "summarize-noplan")

    await worker.run_once()

    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.error_code == "SUMMARIZE_PLAN_REQUIRED"
    assert _memory_rows(migrated_engine) == []


@pytest.mark.asyncio
async def test_summarize_handler_refuses_cloud_profile_without_writing(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    deterministic_uuid7_factory,
    seeded: dict[str, str],
) -> None:
    runner, worker = _worker(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)
    job = runner.enqueue(
        JobKind.SUMMARIZE,
        PROJECT_ID,
        CHAPTER_ID,
        "summarize-cloud",
        plan=_plan(CHAPTER_ID, QWEN_PROFILE_ID, REVISION_ID),
    )

    await worker.run_once()

    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.error_code == "SUMMARIZE_REQUIRES_CLOUD_PROVIDER"
    assert _memory_rows(migrated_engine) == []


@pytest.mark.asyncio
async def test_summarize_handler_is_idempotent_across_repeated_jobs(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    deterministic_uuid7_factory,
    seeded: dict[str, str],
) -> None:
    runner, worker = _worker(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)
    first = runner.enqueue(
        JobKind.SUMMARIZE,
        PROJECT_ID,
        CHAPTER_ID,
        "summarize-first",
        plan=_plan(CHAPTER_ID, FAKE_PROFILE_ID, REVISION_ID),
    )
    second = runner.enqueue(
        JobKind.SUMMARIZE,
        PROJECT_ID,
        CHAPTER_ID,
        "summarize-second",
        plan=_plan(CHAPTER_ID, FAKE_PROFILE_ID, REVISION_ID),
    )

    await worker.run_once()
    await worker.run_once()

    assert runner.get(first.id).status is JobStatus.SUCCEEDED
    assert runner.get(second.id).status is JobStatus.SUCCEEDED
    assert len(_memory_rows(migrated_engine)) == 1


@pytest.mark.asyncio
async def test_summarize_handler_falls_back_to_normalized_source_without_run(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    deterministic_uuid7_factory,
    seeded: dict[str, str],
) -> None:
    runner, worker = _worker(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)
    job = runner.enqueue(
        JobKind.SUMMARIZE,
        PROJECT_ID,
        OTHER_CHAPTER_ID,
        "summarize-source-only",
        plan=_plan(OTHER_CHAPTER_ID, FAKE_PROFILE_ID, OTHER_REVISION_ID),
    )

    await worker.run_once()

    assert runner.get(job.id).status is JobStatus.SUCCEEDED
    rows = _memory_rows(migrated_engine)
    assert len(rows) == 1
    assert rows[0]["entity_key"] == "chapter:2"
    assert rows[0]["summary"] == OTHER_SOURCE_TEXT
    assert rows[0]["status"] == "CANDIDATE"
