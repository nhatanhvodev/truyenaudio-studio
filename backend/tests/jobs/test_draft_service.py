from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.drafts import create_drafts_router
from app.contracts import ChapterState, ImportKind, JobKind, JobStatus, RightsStatus, RunStatus, SourceType
from app.db.base import session_factory
from app.db.models import Chapter, Job, Project, SourceRevision, SourceSegment, TranslationRun, TranslationSegment
from app.modules.translation.draft_service import draft_snapshot
from app.settings.config import Settings


def _seed(db_session) -> dict[str, str]:
    project = Project(
        id="018f0000-0000-7000-8000-000000000601",
        title="T",
        slug="draft-service",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
        default_language="zh-CN",
        target_language="vi-VN",
    )
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(
        id="018f0000-0000-7000-8000-000000000602",
        project_id=project.id,
        ordinal=1,
        state=ChapterState.TRANSLATION_REVIEW.value,
    )
    db_session.add(chapter)
    db_session.flush()
    revision = SourceRevision(
        id="018f0000-0000-7000-8000-000000000603",
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text="一\n二",
        normalized_sha256=hashlib.sha256("一\n二".encode("utf-8")).hexdigest(),
        han_char_count=0,
        total_char_count=3,
        normalizer_version="nfc-v1",
    )
    db_session.add(revision)
    db_session.flush()
    chapter.active_source_revision_id = revision.id
    run = TranslationRun(
        id="018f0000-0000-7000-8000-000000000604",
        chapter_id=chapter.id,
        source_revision_id=revision.id,
        prompt_version="translation-v1",
        status=RunStatus.REVIEW.value,
        translation_text_sha256="a" * 64,
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    db_session.add(run)
    db_session.flush()
    for index, (source_text, target_text) in enumerate((("一", "Một"), ("二", "Hai"))):
        segment = SourceSegment(
            id=f"018f0000-0000-7000-8000-00000000061{index}",
            source_revision_id=revision.id,
            segment_index=index,
            paragraph_start=index,
            paragraph_end=index,
            source_text=source_text,
            source_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            segment_kind="SOURCE",
        )
        db_session.add(segment)
        db_session.flush()
        db_session.add(
            TranslationSegment(
                id=f"018f0000-0000-7000-8000-00000000062{index}",
                translation_run_id=run.id,
                source_segment_id=segment.id,
                target_text=target_text,
                target_sha256=hashlib.sha256(target_text.encode("utf-8")).hexdigest(),
                was_cache_hit=False,
                manually_edited=False,
            )
        )
    job = Job(
        id="018f0000-0000-7000-8000-000000000630",
        kind=JobKind.TRANSLATE.value,
        status=JobStatus.SUCCEEDED.value,
        project_id=project.id,
        chapter_id=chapter.id,
        idempotency_key="draft-job",
        priority=100,
        progress_current=2,
        progress_total=2,
    )
    db_session.add(job)
    db_session.commit()
    return {"project_id": project.id, "chapter_id": chapter.id, "job_id": job.id}


def test_draft_snapshot_replays_stored_segments(db_session) -> None:
    seed = _seed(db_session)

    snapshot = draft_snapshot(db_session, seed["job_id"])

    assert snapshot["text"] == "MộtHai"
    assert snapshot["offset"] == len("MộtHai")
    assert snapshot["status"] == "FINISHED"
    assert snapshot["approvable"] is False
    # Reconnect with a client offset behind the server returns the full text.
    assert draft_snapshot(db_session, seed["job_id"], 2)["resync"] is False
    # A client ahead of the server is told to resync.
    assert draft_snapshot(db_session, seed["job_id"], 999)["resync"] is True


def test_draft_endpoint_returns_snapshot_and_404_for_unknown_job(migrated_engine: Engine, tmp_path: Path) -> None:
    db_path = Path(migrated_engine.url.database)
    session = session_factory(migrated_engine)()
    try:
        seed = _seed(session)
    finally:
        session.close()

    app = FastAPI()
    app.include_router(create_drafts_router(Settings(data_root=db_path.parent)))

    with TestClient(app) as client:
        response = client.get(f"/api/jobs/{seed['job_id']}/draft", params={"afterOffset": 1})
        assert response.status_code == 200
        payload = response.json()
        assert payload["text"] == "MộtHai"
        assert payload["approvable"] is False

        missing = client.get("/api/jobs/018f0000-0000-7000-8000-000000000999/draft")
        assert missing.status_code == 404
