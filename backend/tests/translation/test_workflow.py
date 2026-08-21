from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app.api.translation import create_translation_router
from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ChapterState,
    ImportKind,
    QaCategory,
    QaSeverity,
    RunStatus,
    SourceType,
    RightsStatus,
    TranslationResult,
    Usage,
    UsageUnit,
)
from app.db.models import (
    Artifact,
    Chapter,
    Export,
    GlossaryEntry,
    Project,
    ProviderProfile,
    SourceRevision,
    SourceSegment,
    UsageLedger,
    VoicePlan,
    VoicePreset,
)
from app.db.base import create_engine_for, session_factory
from app.modules.translation.workflow import (
    ApprovalBlocked,
    RevisionConflict,
    TranslationWorkflow,
)
from app.settings.config import Settings


def test_empty_target_is_critical_and_blocks_approval(db_session) -> None:
    fixture = _translated_chapter(db_session)
    workflow = TranslationWorkflow(db_session, id_factory=_ids())

    run = workflow.revise_segment(
        fixture.chapter_id,
        fixture.run_id,
        fixture.segment_id,
        "",
        expected_run_hash=fixture.run_sha256,
    )

    assert any(
        issue.category == QaCategory.COMPLETENESS
        and issue.severity == QaSeverity.CRITICAL
        for issue in run.issues
    )
    with pytest.raises(ApprovalBlocked):
        workflow.approve_revision(fixture.chapter_id, run.id, run.sha256)


def test_manual_edit_creates_new_review_run(db_session) -> None:
    fixture = _approved_chapter(db_session)
    workflow = TranslationWorkflow(db_session, id_factory=_ids())

    revised = workflow.revise_segment(
        fixture.chapter_id,
        fixture.run_id,
        fixture.segment_id,
        "Ban sua 42",
        expected_run_hash=fixture.run_sha256,
    )

    assert revised.id != fixture.run_id
    assert revised.status is RunStatus.REVIEW
    assert (
        db_session.get(Chapter, fixture.chapter_id).state
        == ChapterState.TRANSLATION_REVIEW.value
    )


def test_revision_invalidates_voice_audio_and_export_pointers(db_session) -> None:
    fixture = _approved_chapter(db_session)
    chapter = db_session.get(Chapter, fixture.chapter_id)
    db_session.add(
        VoicePreset(
            id="018f0000-0000-7000-8000-000000000900",
            name="Narrator",
            locale="vi-VN",
            origin="BUILT_IN",
            speed="1.0",
            pitch="0",
            sample_rate=44_100,
            active=True,
        )
    )
    db_session.flush()
    db_session.add(
        VoicePlan(
            id="018f0000-0000-7000-8000-000000000901",
            chapter_id=chapter.id,
            revision_no=1,
            mode="SINGLE_NARRATOR",
            narrator_preset_id="018f0000-0000-7000-8000-000000000900",
            plan_sha256="a" * 64,
        )
    )
    db_session.flush()
    chapter.active_voice_plan_id = "018f0000-0000-7000-8000-000000000901"
    chapter.audio_approved_at = chapter.translation_approved_at
    db_session.add(
        Artifact(
            id="018f0000-0000-7000-8000-000000000902",
            chapter_id=chapter.id,
            kind=ArtifactKind.MASTER_MP3.value,
            status=ArtifactStatus.READY.value,
            relative_path="audio/old/master.mp3",
            sha256="b" * 64,
            byte_size=10,
            mime_type="audio/mpeg",
            input_hash="c" * 64,
            settings_hash="d" * 64,
        )
    )
    db_session.flush()
    chapter.approved_master_artifact_id = "018f0000-0000-7000-8000-000000000902"
    db_session.add(
        Export(
            id="018f0000-0000-7000-8000-000000000903",
            chapter_id=chapter.id,
            kind="PUBLICATION_BUNDLE",
            status="READY",
            manifest_sha256="e" * 64,
        )
    )
    db_session.flush()
    chapter.last_export_id = "018f0000-0000-7000-8000-000000000903"
    db_session.commit()
    workflow = TranslationWorkflow(db_session, id_factory=_ids())

    workflow.revise_segment(
        fixture.chapter_id,
        fixture.run_id,
        fixture.segment_id,
        "Ban sua 42",
        expected_run_hash=fixture.run_sha256,
    )

    chapter = db_session.get(Chapter, fixture.chapter_id)
    assert chapter.approved_translation_run_id is None
    assert chapter.active_voice_plan_id is None
    assert chapter.approved_master_artifact_id is None
    assert chapter.last_export_id is None
    assert chapter.audio_approved_at is None


def test_superseded_run_cannot_be_approved_after_manual_revision(db_session) -> None:
    fixture = _approved_chapter(db_session)
    workflow = TranslationWorkflow(db_session, id_factory=_ids())

    workflow.revise_segment(
        fixture.chapter_id,
        fixture.run_id,
        fixture.segment_id,
        "Lam Dong 42",
        expected_run_hash=fixture.run_sha256,
    )

    with pytest.raises(RevisionConflict):
        workflow.approve_revision(
            fixture.chapter_id, fixture.run_id, fixture.run_sha256
        )
    chapter = db_session.get(Chapter, fixture.chapter_id)
    assert chapter.state == ChapterState.TRANSLATION_REVIEW.value
    assert chapter.approved_translation_run_id is None


def test_revision_requires_expected_run_hash(db_session) -> None:
    fixture = _translated_chapter(db_session)
    workflow = TranslationWorkflow(db_session, id_factory=_ids())

    with pytest.raises(RevisionConflict):
        workflow.revise_segment(
            fixture.chapter_id,
            fixture.run_id,
            fixture.segment_id,
            "Ban sua",
            expected_run_hash="0" * 64,
        )


def test_translation_api_rejects_stale_revision_hash(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "studio.sqlite3"
    backend_root = Path(__file__).parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    command.upgrade(config, "head")

    engine = create_engine_for(db_path)
    with session_factory(engine)() as session:
        fixture = _translated_chapter(session)
        chapter_id = fixture.chapter_id
        run_id = fixture.run_id
        segment_id = fixture.segment_id
    engine.dispose()

    app = FastAPI()
    app.include_router(create_translation_router(Settings(data_root=tmp_path)))

    with TestClient(app) as client:
        response = client.patch(
            f"/api/chapters/{chapter_id}/translation/segments/{segment_id}",
            json={
                "runId": run_id,
                "targetText": "Ban stale",
                "expectedRunHash": "0" * 64,
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "TRANSLATION_RUN_HASH_MISMATCH"


def test_translation_api_qwen_route_requires_consent_and_authorization(
    tmp_path: Path,
) -> None:
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "studio.sqlite3"
    backend_root = Path(__file__).parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    command.upgrade(config, "head")

    engine = create_engine_for(db_path)
    with session_factory(engine)() as session:
        fixture = _source_chapter(session)
        chapter_id = fixture.chapter_id
    engine.dispose()

    app = FastAPI()
    app.include_router(create_translation_router(Settings(data_root=tmp_path)))

    with TestClient(app) as client:
        response = client.post(
            f"/api/chapters/{chapter_id}/translation/qwen",
            json={},
        )

    assert response.status_code == 422
    assert "CLOUD_CONSENT_REQUIRED" in response.json()["detail"]


def test_translation_workflow_passes_cloud_context_to_translator(db_session) -> None:
    fixture = _source_chapter(db_session)
    provider = CountingTranslator("Ban dich 42")
    workflow = TranslationWorkflow(db_session, translator=provider, id_factory=_ids())

    workflow.enqueue_translation(
        fixture.chapter_id,
        cloud_consent_id="018f0000-0000-7000-8000-000000001001",
        budget_authorization_id="018f0000-0000-7000-8000-000000001002",
    )

    assert (
        provider.last_request.context.cloud_consent_id
        == "018f0000-0000-7000-8000-000000001001"
    )
    assert (
        provider.last_request.context.budget_authorization_id
        == "018f0000-0000-7000-8000-000000001002"
    )


def test_cached_translation_creates_segment_without_usage_charge(db_session) -> None:
    fixture = _source_chapter(db_session)
    provider = CountingTranslator("Ban dich 42")
    workflow = TranslationWorkflow(db_session, translator=provider, id_factory=_ids())

    first = workflow.enqueue_translation(fixture.chapter_id)
    second = workflow.enqueue_translation(fixture.chapter_id)

    assert provider.calls == 1
    assert [segment.was_cache_hit for segment in second.segments] == [True]
    assert db_session.scalar(select(UsageLedger)) is None
    assert first.segments[0].cache_key == second.segments[0].cache_key


def test_approval_records_hash_and_translation_state(db_session) -> None:
    fixture = _translated_chapter(db_session, target_text="Lam Dong 42")
    workflow = TranslationWorkflow(db_session, id_factory=_ids())

    approved = workflow.approve_revision(
        fixture.chapter_id, fixture.run_id, fixture.run_sha256
    )

    chapter = db_session.get(Chapter, fixture.chapter_id)
    assert approved.status is RunStatus.APPROVED
    assert chapter.approved_translation_run_id == fixture.run_id
    assert chapter.state == ChapterState.TRANSLATION_APPROVED.value


class CountingTranslator:
    def __init__(self, target_text: str) -> None:
        self.target_text = target_text
        self.calls = 0
        self.last_request = None

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": "fake",
            "model": "fake-translator",
            "region": "local",
            "network": False,
        }

    async def translate(self, request) -> TranslationResult:
        self.calls += 1
        self.last_request = request
        return TranslationResult(
            target_text=self.target_text,
            provider="fake",
            model="fake-translator",
            provider_version="1",
            usage=(Usage(UsageUnit.CHARACTER.value, len(request.source_text)),),
        )


class _Fixture:
    def __init__(
        self, chapter_id: str, run_id: str, segment_id: str, run_sha256: str
    ) -> None:
        self.chapter_id = chapter_id
        self.run_id = run_id
        self.segment_id = segment_id
        self.run_sha256 = run_sha256


def _source_chapter(db_session) -> _Fixture:
    project = _project(db_session)
    chapter = _chapter(db_session, project.id, ChapterState.NORMALIZED)
    revision = _revision(db_session, chapter.id, "林动 has 42 coins.")
    chapter.active_source_revision_id = revision.id
    segment = _segment(db_session, revision.id, revision.normalized_text)
    _locked_term(db_session, project.id, "林动", "Lam Dong")
    _provider_profile(db_session, project.id)
    db_session.commit()
    return _Fixture(chapter.id, "", segment.id, "")


def _translated_chapter(
    db_session, target_text: str = "Lam Dong co 42 dong."
) -> _Fixture:
    fixture = _source_chapter(db_session)
    workflow = TranslationWorkflow(
        db_session, translator=CountingTranslator(target_text), id_factory=_ids()
    )
    run = workflow.enqueue_translation(fixture.chapter_id)
    return _Fixture(fixture.chapter_id, run.id, fixture.segment_id, run.sha256)


def _approved_chapter(db_session) -> _Fixture:
    fixture = _translated_chapter(db_session)
    workflow = TranslationWorkflow(db_session, id_factory=_ids())
    workflow.approve_revision(fixture.chapter_id, fixture.run_id, fixture.run_sha256)
    return fixture


def _project(db_session) -> Project:
    project = Project(
        id="018f0000-0000-7000-8000-100000000001",
        title="Truyen",
        slug="truyen",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
        style_guide_text="Plain Vietnamese narration.",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _chapter(db_session, project_id: str, state: ChapterState) -> Chapter:
    chapter = Chapter(
        id="018f0000-0000-7000-8000-200000000001",
        project_id=project_id,
        ordinal=1,
        source_title="一",
        state=state.value,
    )
    db_session.add(chapter)
    db_session.flush()
    return chapter


def _revision(db_session, chapter_id: str, text: str) -> SourceRevision:
    revision = SourceRevision(
        id="018f0000-0000-7000-8000-300000000001",
        chapter_id=chapter_id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text=text,
        normalized_sha256=_sha(text),
        han_char_count=2,
        total_char_count=len(text),
        normalizer_version="nfc-v1",
    )
    db_session.add(revision)
    db_session.flush()
    return revision


def _segment(db_session, revision_id: str, text: str) -> SourceSegment:
    segment = SourceSegment(
        id="018f0000-0000-7000-8000-400000000001",
        source_revision_id=revision_id,
        segment_index=0,
        paragraph_start=0,
        paragraph_end=0,
        source_text=text,
        source_sha256=_sha(text),
        segment_kind="SOURCE",
    )
    db_session.add(segment)
    db_session.flush()
    return segment


def _locked_term(db_session, project_id: str, source: str, target: str) -> None:
    db_session.add(
        GlossaryEntry(
            id="018f0000-0000-7000-8000-500000000001",
            project_id=project_id,
            source_term=source,
            target_term=target,
            is_locked=True,
            revision_no=1,
        )
    )
    db_session.flush()


def _provider_profile(db_session, project_id: str) -> None:
    profile = ProviderProfile(
        id="018f0000-0000-7000-8000-600000000001",
        provider_kind="TRANSLATOR",
        adapter_name="fake",
        display_name="Fake Translator",
        model="fake-translator",
        region="local",
        enabled=True,
    )
    db_session.add(profile)
    db_session.flush()
    db_session.get(Project, project_id).default_translator_profile_id = profile.id
    db_session.flush()


_ID_COUNTER = 0


def _ids():
    def factory() -> str:
        global _ID_COUNTER
        _ID_COUNTER += 1
        return f"018f0000-0000-7000-8000-900000{_ID_COUNTER:06d}"

    return factory


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
