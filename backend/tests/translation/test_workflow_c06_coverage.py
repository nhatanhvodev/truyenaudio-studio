from __future__ import annotations

import hashlib

import pytest

from app.contracts import ChapterState, ImportKind, RightsStatus, RunStatus, SourceType
from app.db.models import Chapter, Project, SourceRevision, SourceSegment, TranslationRun, TranslationSegment
from app.modules.translation.workflow import ApprovalBlocked, TranslationWorkflow


def test_approve_blocks_when_run_misses_a_source_segment(db_session) -> None:
    fixture = _workflow_fixture(db_session, covered=("seg-1",))
    workflow = TranslationWorkflow(db_session, id_factory=_ids())

    with pytest.raises(ApprovalBlocked, match="TRANSLATION_RUN_SEGMENTS_INCOMPLETE:missing=1,extra=0"):
        workflow.approve_revision(
            fixture["chapter_id"],
            fixture["run_id"],
            fixture["run_hash"],
        )


def test_approve_blocks_when_run_has_extra_segment(db_session) -> None:
    fixture = _workflow_fixture(db_session, covered=("seg-1", "seg-2", "seg-extra"))
    workflow = TranslationWorkflow(db_session, id_factory=_ids())

    with pytest.raises(ApprovalBlocked, match="TRANSLATION_RUN_SEGMENTS_INCOMPLETE:missing=0,extra=1"):
        workflow.approve_revision(
            fixture["chapter_id"],
            fixture["run_id"],
            fixture["run_hash"],
        )


def test_approve_succeeds_with_full_segment_coverage(db_session) -> None:
    fixture = _workflow_fixture(db_session, covered=("seg-1", "seg-2"))
    workflow = TranslationWorkflow(db_session, id_factory=_ids())

    view = workflow.approve_revision(
        fixture["chapter_id"],
        fixture["run_id"],
        fixture["run_hash"],
    )

    assert view.status == RunStatus.APPROVED.value
    assert db_session.get(Chapter, fixture["chapter_id"]).approved_translation_run_id == fixture["run_id"]


def test_revise_and_reapprove_still_enforces_coverage(db_session) -> None:
    fixture = _workflow_fixture(db_session, covered=("seg-1",))
    workflow = TranslationWorkflow(db_session, id_factory=_ids())
    revised = workflow.revise_segment(
        fixture["chapter_id"],
        fixture["run_id"],
        fixture["segments"]["seg-1"],
        "Bản sửa.",
        expected_run_hash=fixture["run_hash"],
    )

    new_run = db_session.get(TranslationRun, revised.id)
    with pytest.raises(ApprovalBlocked, match="TRANSLATION_RUN_SEGMENTS_INCOMPLETE"):
        workflow.approve_revision(
            fixture["chapter_id"],
            revised.id,
            new_run.translation_text_sha256 or ("0" * 64),
        )


def _workflow_fixture(db_session, *, covered: tuple[str, ...]) -> dict[str, object]:
    project = Project(
        id=_next_id(),
        title="Truyen",
        slug="c06-qa",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
        default_language="zh-CN",
        target_language="vi-VN",
    )
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(
        id=_next_id(),
        project_id=project.id,
        ordinal=1,
        state=ChapterState.TRANSLATION_REVIEW.value,
    )
    db_session.add(chapter)
    db_session.flush()
    revision = SourceRevision(
        id=_next_id(),
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
    segments: dict[str, str] = {}
    for index, key in enumerate(("seg-1", "seg-2")):
        text = "一" if key == "seg-1" else "二"
        segment = SourceSegment(
            id=_next_id(),
            source_revision_id=revision.id,
            segment_index=index,
            paragraph_start=index,
            paragraph_end=index,
            source_text=text,
            source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            segment_kind="SOURCE",
        )
        db_session.add(segment)
        segments[key] = segment.id
    if "seg-extra" in covered:
        extra_revision = SourceRevision(
            id=_next_id(),
            chapter_id=chapter.id,
            revision_no=2,
            import_kind=ImportKind.PASTE.value,
            normalized_text="三",
            normalized_sha256=hashlib.sha256("三".encode("utf-8")).hexdigest(),
            han_char_count=0,
            total_char_count=1,
            normalizer_version="nfc-v1",
        )
        db_session.add(extra_revision)
        extra_segment = SourceSegment(
            id=_next_id(),
            source_revision_id=extra_revision.id,
            segment_index=0,
            paragraph_start=0,
            paragraph_end=0,
            source_text="三",
            source_sha256=hashlib.sha256("三".encode("utf-8")).hexdigest(),
            segment_kind="SOURCE",
        )
        db_session.add(extra_segment)
        segments["seg-extra"] = extra_segment.id
    db_session.flush()
    run = TranslationRun(
        id=_next_id(),
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
    for key in covered:
        source_id = segments[key]
        text = "一" if key == "seg-1" else ("二" if key == "seg-2" else "三")
        db_session.add(
            TranslationSegment(
                id=_next_id(),
                translation_run_id=run.id,
                source_segment_id=source_id,
                target_text="Bản dịch " + key,
                target_sha256=hashlib.sha256(("Bản dịch " + key).encode("utf-8")).hexdigest(),
                was_cache_hit=False,
                manually_edited=False,
            )
        )
    db_session.flush()
    return {
        "project_id": project.id,
        "chapter_id": chapter.id,
        "run_id": run.id,
        "run_hash": run.translation_text_sha256 or ("a" * 64),
        "segments": segments,
    }


_SEQ = [0]


def _next_id() -> str:
    _SEQ[0] += 1
    return f"018f0000-0000-7000-8000-{_SEQ[0]:012x}"


def _ids():
    def factory() -> str:
        return _next_id()

    return factory
