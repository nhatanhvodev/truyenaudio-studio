from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.contracts import (
    ChapterState,
    ImportKind,
    JobKind,
    JobStatus,
    ProviderKind,
    RightsStatus,
    SourceType,
)
from app.modules.jobs.execution_handlers import build_translate_handler
from app.modules.jobs.runner import JobRunner
from app.settings.config import Settings
from app.worker import Worker


@pytest.fixture
def worker_db_path(migrated_engine: Engine) -> Path:
    return Path(migrated_engine.url.database)


def _seed_chapter(engine: Engine) -> dict[str, str]:
    project_id = "018f0000-0000-7000-8000-000000000301"
    chapter_id = "018f0000-0000-7000-8000-000000000302"
    profile_id = "018f0000-0000-7000-8000-000000000303"
    revision_id = "018f0000-0000-7000-8000-000000000304"
    now = "2026-08-19T00:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects (id, title, slug, source_type, rights_status, default_language, "
                "target_language, created_at, updated_at) VALUES (:id, 'T', 'exec-handler', :st, :rs, "
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
                "INSERT INTO provider_profiles (id, provider_kind, adapter_name, display_name, model, "
                "region, enabled, created_at, updated_at) VALUES (:id, :kind, 'fake-hanviet-v2', 'Fake', "
                "'fake-hanviet-v2', 'local', 1, :now, :now)"
            ),
            {"id": profile_id, "kind": ProviderKind.TRANSLATOR.value, "now": now},
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
                "body": "一\n二",
                "sha": hashlib.sha256("一\n二".encode("utf-8")).hexdigest(),
                "now": now,
            },
        )
        connection.execute(
            text("UPDATE chapters SET active_source_revision_id = :rid WHERE id = :cid"),
            {"rid": revision_id, "cid": chapter_id},
        )
        for index, text_value in enumerate(("一", "二")):
            connection.execute(
                text(
                    "INSERT INTO source_segments (id, source_revision_id, segment_index, paragraph_start, "
                    "paragraph_end, source_text, source_sha256, segment_kind, created_at, updated_at) "
                    "VALUES (:id, :rid, :idx, :idx, :idx, :text, :sha, 'SOURCE', :now, :now)"
                ),
                {
                    "id": f"018f0000-0000-7000-8000-0000000003{20 + index:02d}",
                    "rid": revision_id,
                    "idx": index,
                    "text": text_value,
                    "sha": hashlib.sha256(text_value.encode("utf-8")).hexdigest(),
                    "now": now,
                },
            )
    return {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "revision_id": revision_id,
        "profile_id": profile_id,
    }


def _worker_for(
    migrated_engine: Engine,
    worker_db_path: Path,
    tmp_path: Path,
    deterministic_uuid7_factory,
) -> Worker:
    settings = Settings(data_root=tmp_path)
    runner = JobRunner(migrated_engine, id_factory=deterministic_uuid7_factory)
    handler = build_translate_handler(settings, db_path=worker_db_path)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    return runner, Worker(runner, handlers={JobKind.TRANSLATE: handler}, artifact_root=artifact_root)


@pytest.mark.asyncio
async def test_translate_job_runs_fake_provider_to_completion(
    worker_db_path: Path, migrated_engine: Engine, deterministic_uuid7_factory, tmp_path: Path
) -> None:
    seed = _seed_chapter(migrated_engine)
    runner, worker = _worker_for(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)

    job = runner.enqueue(
        JobKind.TRANSLATE,
        seed["project_id"],
        seed["chapter_id"],
        "exec-happy",
        plan={
            "projectId": seed["project_id"],
            "profileId": seed["profile_id"],
            "cloudConsentId": "consent-1",
            "budgetAuthorizationId": "auth-1",
            "revisionId": seed["revision_id"],
        },
    )

    await worker.run_once()

    assert runner.get(job.id).status is JobStatus.SUCCEEDED
    with migrated_engine.connect() as connection:
        count = connection.execute(
            text(
                "SELECT COUNT(*) FROM translation_runs WHERE chapter_id = :cid"
            ),
            {"cid": seed["chapter_id"]},
        ).scalar_one()
        assert count == 1


@pytest.mark.asyncio
async def test_translate_job_without_plan_fails_closed(
    worker_db_path: Path, migrated_engine: Engine, deterministic_uuid7_factory, tmp_path: Path
) -> None:
    seed = _seed_chapter(migrated_engine)
    runner, worker = _worker_for(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)

    job = runner.enqueue(JobKind.TRANSLATE, seed["project_id"], seed["chapter_id"], "exec-noplan")

    await worker.run_once()

    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.error_code == "TRANSLATE_PLAN_REQUIRED"


@pytest.mark.asyncio
async def test_translate_job_rejects_stale_revision(
    worker_db_path: Path, migrated_engine: Engine, deterministic_uuid7_factory, tmp_path: Path
) -> None:
    seed = _seed_chapter(migrated_engine)
    runner, worker = _worker_for(migrated_engine, worker_db_path, tmp_path, deterministic_uuid7_factory)

    job = runner.enqueue(
        JobKind.TRANSLATE,
        seed["project_id"],
        seed["chapter_id"],
        "exec-stale",
        plan={
            "projectId": seed["project_id"],
            "profileId": seed["profile_id"],
            "cloudConsentId": "consent-1",
            "budgetAuthorizationId": "auth-1",
            "revisionId": "0" * 36,
        },
    )

    await worker.run_once()

    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.error_code == "TRANSLATE_REVISION_STALE"
