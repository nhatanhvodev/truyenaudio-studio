from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ChapterState,
    CloudConsentStatus,
    ImportKind,
    JobKind,
    JobStatus,
    ProviderKind,
    RightsScope,
    RightsStatus,
    SourceType,
    Usage,
    UsageUnit,
)
from app.db.models import (
    Artifact,
    BudgetAuthorization,
    Chapter,
    CloudProcessingConsent,
    Job,
    Project,
    ProviderProfile,
    RateCard,
    RightsGrant,
    SourceRevision,
)
from app.modules.budgets.guard import BudgetGuard
from app.modules.compliance.cloud import CloudCallGuard
from app.modules.jobs.batch import BatchCloudAuthorization, BatchCoordinator
from app.modules.jobs.runner import JobRunner


NOW = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
PROFILE_ID = "018f0000-0000-7000-8000-000000005001"
CONSENT_ID = "018f0000-0000-7000-8000-000000005002"
POLICY_ARTIFACT_ID = "018f0000-0000-7000-8000-000000005003"
RATE_CARD_ID = "018f0000-0000-7000-8000-000000005004"
BUDGET_AUTH_ID = "018f0000-0000-7000-8000-000000005005"
POLICY_HASH = "a" * 64


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

    view = batch.enqueue_batch(project_with_50.id, chapter_ids, JobKind.EXPORT, "quote")

    assert len(view.job_ids) == 50
    assert batch.enqueue_batch(project_with_50.id, chapter_ids, JobKind.EXPORT, "quote").job_ids == view.job_ids
    rows = db_session.scalars(select(Job).where(Job.project_id == project_with_50.id)).all()
    assert len(rows) == 50
    assert {row.status for row in rows} == {JobStatus.QUEUED.value}
    assert {row.kind for row in rows} == {JobKind.EXPORT.value}
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


def test_cloud_translate_batch_validates_existing_consent_and_budget_before_enqueue(
    batch: BatchCoordinator,
    project_with_50: Project,
    db_session: Session,
) -> None:
    _seed_cloud_authorization(db_session, project_with_50.id)
    chapter_ids = tuple(
        db_session.scalars(
            select(Chapter.id)
            .where(Chapter.project_id == project_with_50.id)
            .order_by(Chapter.ordinal.asc(), Chapter.id.asc())
            .limit(2)
        )
    )
    guard = CloudCallGuard(db_session, BudgetGuard(db_session, now=lambda: NOW), now=lambda: NOW)

    view = batch.enqueue_batch(
        project_with_50.id,
        chapter_ids,
        JobKind.TRANSLATE,
        "batch-cloud-quote",
        cloud_authorization=BatchCloudAuthorization(
            provider_profile_id=PROFILE_ID,
            cloud_consent_id=CONSENT_ID,
            budget_authorization_id=BUDGET_AUTH_ID,
            estimated_usage=(Usage(UsageUnit.INPUT_TOKEN.value, 100),),
        ),
        cloud_guard=guard,
    )

    assert len(view.job_ids) == 2
    assert view.blocked == 0


def test_cloud_translate_batch_rejects_missing_guard_inputs_before_enqueue(
    batch: BatchCoordinator,
    project_with_50: Project,
    db_session: Session,
) -> None:
    chapter_id = db_session.scalar(select(Chapter.id).where(Chapter.project_id == project_with_50.id).limit(1))
    assert chapter_id is not None

    with pytest.raises(ValueError, match="BATCH_CLOUD_AUTHORIZATION_REQUIRED"):
        batch.enqueue_batch(project_with_50.id, (chapter_id,), JobKind.TRANSLATE, "batch-cloud-quote")

    assert db_session.scalar(select(Job).where(Job.project_id == project_with_50.id).limit(1)) is None


def test_pause_blocks_subsequent_child_jobs_without_canceling_existing_children(
    batch: BatchCoordinator,
    project_with_50: Project,
    db_session: Session,
) -> None:
    chapter_ids = tuple(
        db_session.scalars(
            select(Chapter.id)
            .where(Chapter.project_id == project_with_50.id)
            .order_by(Chapter.ordinal.asc(), Chapter.id.asc())
            .limit(3)
        )
    )

    view = batch.enqueue_batch(
        project_with_50.id,
        chapter_ids,
        JobKind.EXPORT,
        "pause-after-one",
        pause_requested=lambda _batch_id, created_count: created_count >= 1,
    )

    assert view.paused is True
    assert len(view.job_ids) == 1
    stored = db_session.get(Job, view.job_ids[0])
    assert stored is not None
    assert stored.status == JobStatus.QUEUED.value


def _seed_cloud_authorization(db_session: Session, project_id: str) -> None:
    db_session.add(
        ProviderProfile(
            id=PROFILE_ID,
            provider_kind=ProviderKind.TRANSLATOR.value,
            adapter_name="qwen-mt",
            display_name="Qwen MT Flash",
            model="qwen-mt-flash",
            region="frankfurt",
            secret_ref="env:QWEN_API_KEY",
            config_json={"provider": "qwen", "policy_sha256": POLICY_HASH},
            enabled=True,
        )
    )
    db_session.add(
        Artifact(
            id=POLICY_ARTIFACT_ID,
            kind=ArtifactKind.LICENSE_SNAPSHOT.value,
            status=ArtifactStatus.READY.value,
            relative_path="projects/batch/qwen-policy.json",
            sha256=POLICY_HASH,
            byte_size=32,
            mime_type="application/json",
            input_hash="1" * 64,
            settings_hash="2" * 64,
        )
    )
    db_session.flush()
    db_session.add(
        CloudProcessingConsent(
            id=CONSENT_ID,
            project_id=project_id,
            provider_profile_id=PROFILE_ID,
            status=CloudConsentStatus.GRANTED.value,
            policy_snapshot_artifact_id=POLICY_ARTIFACT_ID,
            accepted_at=NOW,
        )
    )
    db_session.add(
        RightsGrant(
            id="018f0000-0000-7000-8000-000000005006",
            project_id=project_id,
            scope=RightsScope.TRANSLATE_VI.value,
            territory="WORLD",
            allows_ai_processing=True,
            allows_third_party_cloud=True,
            valid_from=NOW,
        )
    )
    db_session.add(
        RateCard(
            id=RATE_CARD_ID,
            provider="qwen",
            model="qwen-mt-flash",
            region="frankfurt",
            unit=UsageUnit.INPUT_TOKEN.value,
            price_usd_micros_per_million_units=1_000_000,
            effective_from=NOW,
            source_url="https://example.test/qwen-rate-card",
            source_note="fixture",
            verified_at=NOW,
        )
    )
    db_session.add(
        BudgetAuthorization(
            id=BUDGET_AUTH_ID,
            operation_id="batch-cloud-quote",
            estimate_vnd=10,
            contingency_vnd=2,
            category="REGULAR",
            rate_card_ids_json=[RATE_CARD_ID],
            expires_at=NOW.replace(hour=4),
            status="HELD",
        )
    )
    db_session.commit()
