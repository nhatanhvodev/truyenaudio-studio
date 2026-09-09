from __future__ import annotations

import hashlib

import pytest

from app.contracts import ChapterState, ImportKind, RightsStatus, RunStatus, SourceType
from app.db.models import (
    Chapter,
    Project,
    SourceRevision,
    SourceSegment,
    TranslationRun,
    TranslationSegment,
)
from app.modules.translation.story_memory import StoryMemoryService


def test_candidate_is_never_exposed_as_context(db_session) -> None:
    fixture = _project(db_session)
    service = StoryMemoryService(db_session, id_factory=_ids())

    service.create_candidate(
        fixture["project_id"],
        entity_key="lin-dong",
        entity_type="character",
        summary="CANDIDATE summary must not leak.",
        valid_from_ordinal=1,
    )

    assert service.memory_for(fixture["project_id"], 5) == ()
    assert service.summaries_for(fixture["project_id"], 5) == ()
    assert len(service.candidates_for(fixture["project_id"])) == 1
    assert service.hash_for(fixture["project_id"], 5) == hashlib.sha256(b"[]").hexdigest()


def test_approve_requires_approved_run_and_its_segments(db_session) -> None:
    fixture = _project(db_session)
    service = StoryMemoryService(db_session, id_factory=_ids())
    candidate = service.create_candidate(
        fixture["project_id"],
        entity_key="lin-dong",
        entity_type="character",
        summary="Approve me.",
        valid_from_ordinal=1,
    )

    with pytest.raises(ValueError, match="MEMORY_EVIDENCE_RUN_NOT_APPROVED"):
        service.approve(
            fixture["project_id"],
            candidate.id,
            source_run_id=fixture["review_run_id"],
            evidence_segment_ids=(fixture["segment_id"],),
        )
    with pytest.raises(ValueError, match="MEMORY_EVIDENCE_SEGMENTS_INVALID"):
        service.approve(
            fixture["project_id"],
            candidate.id,
            source_run_id=fixture["approved_run_id"],
            evidence_segment_ids=("wrong-segment",),
        )

    approved = service.approve(
        fixture["project_id"],
        candidate.id,
        source_run_id=fixture["approved_run_id"],
        evidence_segment_ids=(fixture["segment_id"],),
    )

    assert approved.status == "APPROVED"
    assert approved.source_run_id == fixture["approved_run_id"]
    assert approved.evidence_segment_ids == (fixture["segment_id"],)
    assert [entry.entity_key for entry in service.memory_for(fixture["project_id"], 5)] == ["lin-dong"]


def test_rejected_candidate_is_excluded(db_session) -> None:
    fixture = _project(db_session)
    service = StoryMemoryService(db_session, id_factory=_ids())
    candidate = service.create_candidate(
        fixture["project_id"],
        entity_key="bad",
        entity_type="fact",
        summary="Reject.",
        valid_from_ordinal=1,
    )

    rejected = service.reject(fixture["project_id"], candidate.id)

    assert rejected.status == "REJECTED"
    assert service.memory_for(fixture["project_id"], 5) == ()
    assert service.candidates_for(fixture["project_id"]) == ()


def test_approve_rejects_foreign_project_run(db_session) -> None:
    fixture = _project(db_session)
    other = _project(db_session, slug="other")
    service = StoryMemoryService(db_session, id_factory=_ids())
    candidate = service.create_candidate(
        fixture["project_id"],
        entity_key="lin-dong",
        entity_type="character",
        summary="X",
        valid_from_ordinal=1,
    )

    with pytest.raises(ValueError, match="MEMORY_EVIDENCE_RUN_NOT_FOUND"):
        service.approve(
            fixture["project_id"],
            candidate.id,
            source_run_id=other["approved_run_id"],
            evidence_segment_ids=(other["segment_id"],),
        )


def test_memory_becomes_stale_when_evidence_run_is_superseded(db_session) -> None:
    fixture = _project(db_session)
    service = StoryMemoryService(db_session, id_factory=_ids())
    candidate = service.create_candidate(
        fixture["project_id"],
        entity_key="lin-dong",
        entity_type="character",
        summary="Stale me.",
        valid_from_ordinal=1,
    )
    service.approve(
        fixture["project_id"],
        candidate.id,
        source_run_id=fixture["approved_run_id"],
        evidence_segment_ids=(fixture["segment_id"],),
    )
    assert service.memory_for(fixture["project_id"], 5) != ()

    run = db_session.get(TranslationRun, fixture["approved_run_id"])
    run.status = RunStatus.SUPERSEDED.value
    db_session.flush()

    assert service.memory_for(fixture["project_id"], 5) == ()


def _project(db_session, *, slug: str = "memory") -> dict[str, str]:
    project = Project(
        id=_next_id(),
        title="Truyen",
        slug=slug,
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
        state=ChapterState.TRANSLATION_APPROVED.value,
    )
    db_session.add(chapter)
    db_session.flush()
    revision = SourceRevision(
        id=_next_id(),
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text="林动抬头。",
        normalized_sha256=hashlib.sha256("林动抬头。".encode("utf-8")).hexdigest(),
        han_char_count=0,
        total_char_count=5,
        normalizer_version="nfc-v1",
    )
    db_session.add(revision)
    db_session.flush()
    chapter.active_source_revision_id = revision.id
    segment = SourceSegment(
        id=_next_id(),
        source_revision_id=revision.id,
        segment_index=0,
        paragraph_start=0,
        paragraph_end=0,
        source_text="林动抬头。",
        source_sha256=hashlib.sha256("林动抬头。".encode("utf-8")).hexdigest(),
        segment_kind="SOURCE",
    )
    db_session.add(segment)
    db_session.flush()

    def _run(status: RunStatus, text: str, suffix: str) -> str:
        run = TranslationRun(
            id=_next_id(),
            chapter_id=chapter.id,
            source_revision_id=revision.id,
            prompt_version="translation-v1",
            status=status.value,
            translation_text_sha256="a" * 64,
            estimated_cost_vnd=0,
            actual_cost_vnd=0,
        )
        db_session.add(run)
        db_session.flush()
        db_session.add(
            TranslationSegment(
                id=_next_id(),
                translation_run_id=run.id,
                source_segment_id=segment.id,
                target_text=text,
                target_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                was_cache_hit=False,
                manually_edited=False,
            )
        )
        db_session.flush()
        return run.id

    approved_run_id = _run(RunStatus.APPROVED, "Bản dịch đã duyệt.", "a")
    review_run_id = _run(RunStatus.REVIEW, "Bản dịch review.", "r")
    return {
        "project_id": project.id,
        "chapter_id": chapter.id,
        "revision_id": revision.id,
        "segment_id": segment.id,
        "approved_run_id": approved_run_id,
        "review_run_id": review_run_id,
    }


_SEQ = [0]


def _next_id() -> str:
    _SEQ[0] += 1
    return f"018f0000-0000-7000-8000-{_SEQ[0]:012x}"


def _ids():
    def factory() -> str:
        return _next_id()

    return factory
