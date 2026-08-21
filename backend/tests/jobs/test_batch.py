from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.contracts import ChapterState, ImportKind, JobKind, JobStatus, RightsStatus, SourceType
from app.db.models import Chapter, Job, Project, SourceRevision
from app.modules.jobs.batch import BatchCoordinator
from app.modules.jobs.runner import JobRunner


NOW = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)


@pytest.fixture
def project_with_50(db_session: Session) -> Project:
    project = Project(
        id="018f0000-0000-7000-8000-000000001000",
        title="Batch Project",
        slug="batch-project",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
    )
    db_session.add(project)
    db_session.flush()
    for ordinal in range(1, 51):
        chapter = Chapter(
            id=f"018f0000-0000-7000-8000-000000001{ordinal:03d}",
            project_id=project.id,
            ordinal=ordinal,
            source_title=f"Chapter {ordinal}",
            state=ChapterState.NORMALIZED.value,
        )
        db_session.add(chapter)
        db_session.flush()
        revision = SourceRevision(
            id=f"018f0000-0000-7000-8000-000000002{ordinal:03d}",
            chapter_id=chapter.id,
            revision_no=1,
            import_kind=ImportKind.PASTE.value,
            normalized_text=f"Source text {ordinal}",
            normalized_sha256=f"{ordinal:064x}",
            han_char_count=1,
            total_char_count=12,
            normalizer_version="test",
        )
        db_session.add(revision)
        db_session.flush()
        chapter.active_source_revision_id = revision.id
    db_session.commit()
    return project


@pytest.fixture
def batch(migrated_engine: Engine, deterministic_uuid7_factory) -> BatchCoordinator:
    return BatchCoordinator(
        migrated_engine,
        job_runner=JobRunner(migrated_engine, id_factory=deterministic_uuid7_factory),
    )


def test_fifty_chapters_are_fifty_idempotent_jobs(
    batch: BatchCoordinator,
    project_with_50: Project,
    db_session: Session,
) -> None:
    chapter_ids = tuple(
        db_session.scalars(
            select(Chapter.id)
            .where(Chapter.project_id == project_with_50.id)
            .order_by(Chapter.ordinal.asc(), Chapter.id.asc())
        )
    )

    view = batch.enqueue_batch(project_with_50.id, chapter_ids, JobKind.TRANSLATE, "quote")

    assert len(view.job_ids) == 50
    assert batch.enqueue_batch(project_with_50.id, chapter_ids, JobKind.TRANSLATE, "quote").job_ids == view.job_ids
    rows = db_session.scalars(select(Job).where(Job.project_id == project_with_50.id)).all()
    assert len(rows) == 50
    assert {row.status for row in rows} == {JobStatus.QUEUED.value}
    assert {row.kind for row in rows} == {JobKind.TRANSLATE.value}
    assert {row.chapter_id for row in rows} == set(chapter_ids)


def test_batch_rejects_more_than_fifty_chapters(batch: BatchCoordinator, project_with_50: Project) -> None:
    too_many = tuple(f"018f0000-0000-7000-8000-000000009{index:03d}" for index in range(51))

    with pytest.raises(ValueError, match="BATCH_CHAPTER_LIMIT_EXCEEDED"):
        batch.enqueue_batch(project_with_50.id, too_many, JobKind.TRANSLATE, "quote")


def test_batch_rejects_duplicate_chapter_ids(batch: BatchCoordinator, project_with_50: Project, db_session: Session) -> None:
    chapter_id = db_session.scalar(select(Chapter.id).where(Chapter.project_id == project_with_50.id).limit(1))
    assert chapter_id is not None

    with pytest.raises(ValueError, match="BATCH_CHAPTER_DUPLICATE"):
        batch.enqueue_batch(project_with_50.id, (chapter_id, chapter_id), JobKind.TRANSLATE, "quote")

