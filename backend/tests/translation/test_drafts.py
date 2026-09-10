from __future__ import annotations

import hashlib

import pytest

from app.contracts import ChapterState, ImportKind, RightsStatus, RunStatus, SourceType
from app.db.models import Chapter, Project, SourceRevision, SourceSegment, TranslationRun, TranslationSegment
from app.modules.translation.drafts import DraftConflict, restore_draft, save_draft


def _fixture(db_session) -> dict[str, str]:
    project = Project(
        id="018f0000-0000-7000-8000-000000000701",
        title="T",
        slug="drafts",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
        default_language="zh-CN",
        target_language="vi-VN",
    )
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(
        id="018f0000-0000-7000-8000-000000000702",
        project_id=project.id,
        ordinal=1,
        state=ChapterState.TRANSLATION_REVIEW.value,
    )
    db_session.add(chapter)
    db_session.flush()
    revision = SourceRevision(
        id="018f0000-0000-7000-8000-000000000703",
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
    segment_ids: list[str] = []
    for index, text in enumerate(("一", "二")):
        segment = SourceSegment(
            id=f"018f0000-0000-7000-8000-00000000071{index}",
            source_revision_id=revision.id,
            segment_index=index,
            paragraph_start=index,
            paragraph_end=index,
            source_text=text,
            source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            segment_kind="SOURCE",
        )
        db_session.add(segment)
        segment_ids.append(segment.id)
    run = TranslationRun(
        id="018f0000-0000-7000-8000-000000000730",
        chapter_id=chapter.id,
        source_revision_id=revision.id,
        prompt_version="translation-v1",
        status=RunStatus.APPROVED.value,
        translation_text_sha256="a" * 64,
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    db_session.add(run)
    db_session.flush()
    for index, segment_id in enumerate(segment_ids):
        db_session.add(
            TranslationSegment(
                id=f"018f0000-0000-7000-8000-00000000074{index}",
                translation_run_id=run.id,
                source_segment_id=segment_id,
                target_text="Bản dịch gốc.",
                target_sha256=hashlib.sha256("Bản dịch gốc.".encode("utf-8")).hexdigest(),
                was_cache_hit=False,
                manually_edited=False,
            )
        )
    chapter.approved_translation_run_id = run.id
    db_session.commit()
    return {
        "project_id": project.id,
        "chapter_id": chapter.id,
        "revision_id": revision.id,
        "run_id": run.id,
        "segment_a": segment_ids[0],
        "segment_b": segment_ids[1],
    }


def test_create_then_restore_draft_round_trip(db_session) -> None:
    fixture = _fixture(db_session)

    assert restore_draft(db_session, fixture["chapter_id"], fixture["revision_id"]) is None

    created = save_draft(
        db_session,
        fixture["chapter_id"],
        fixture["revision_id"],
        {fixture["segment_a"]: "Nháp một."},
        expected_revision=None,
    )

    assert created.revision == 1
    restored = restore_draft(db_session, fixture["chapter_id"], fixture["revision_id"])
    assert restored is not None
    assert restored.content == {fixture["segment_a"]: "Nháp một."}
    assert restored.revision == 1


def test_stale_expected_revision_is_rejected_without_overwriting(db_session) -> None:
    fixture = _fixture(db_session)
    save_draft(
        db_session,
        fixture["chapter_id"],
        fixture["revision_id"],
        {fixture["segment_a"]: "Nháp một."},
        expected_revision=None,
    )
    save_draft(
        db_session,
        fixture["chapter_id"],
        fixture["revision_id"],
        {fixture["segment_a"]: "Nháp hai."},
        expected_revision=1,
    )

    with pytest.raises(DraftConflict) as caught:
        save_draft(
            db_session,
            fixture["chapter_id"],
            fixture["revision_id"],
            {fixture["segment_a"]: "Ghi đè trái phép."},
            expected_revision=1,
        )

    assert caught.value.code == "DRAFT_REVISION_CONFLICT"
    restored = restore_draft(db_session, fixture["chapter_id"], fixture["revision_id"])
    assert restored is not None
    assert restored.content == {fixture["segment_a"]: "Nháp hai."}
    assert restored.revision == 2


def test_create_conflicts_when_draft_already_exists(db_session) -> None:
    fixture = _fixture(db_session)
    save_draft(
        db_session,
        fixture["chapter_id"],
        fixture["revision_id"],
        {fixture["segment_a"]: "Nháp."},
        expected_revision=None,
    )

    with pytest.raises(DraftConflict):
        save_draft(
            db_session,
            fixture["chapter_id"],
            fixture["revision_id"],
            {fixture["segment_a"]: "Tạo lại."},
            expected_revision=None,
        )


def test_unknown_segment_and_invalid_payload_are_rejected(db_session) -> None:
    fixture = _fixture(db_session)

    with pytest.raises(ValueError, match="DRAFT_SEGMENT_UNKNOWN"):
        save_draft(
            db_session,
            fixture["chapter_id"],
            fixture["revision_id"],
            {"not-a-segment": "x"},
            expected_revision=None,
        )
    with pytest.raises(ValueError, match="DRAFT_TEXT_INVALID"):
        save_draft(
            db_session,
            fixture["chapter_id"],
            fixture["revision_id"],
            {fixture["segment_a"]: 5},  # type: ignore[dict-item]
            expected_revision=None,
        )
    with pytest.raises(ValueError, match="BASE_REVISION_NOT_FOUND"):
        save_draft(
            db_session,
            fixture["chapter_id"],
            "018f0000-0000-7000-8000-000000000999",
            {},
            expected_revision=None,
        )


def test_draft_never_changes_approved_translation(db_session) -> None:
    fixture = _fixture(db_session)

    save_draft(
        db_session,
        fixture["chapter_id"],
        fixture["revision_id"],
        {fixture["segment_a"]: "Nháp khác hẳn.", fixture["segment_b"]: "Nháp hai."},
        expected_revision=None,
    )

    run = db_session.get(TranslationRun, fixture["run_id"])
    assert run is not None
    assert run.status == RunStatus.APPROVED.value
    targets = {
        segment.source_segment_id: segment.target_text
        for segment in db_session.query(TranslationSegment).filter_by(translation_run_id=run.id)
    }
    assert set(targets.values()) == {"Bản dịch gốc."}
    chapter = db_session.get(Chapter, fixture["chapter_id"])
    assert chapter is not None
    assert chapter.approved_translation_run_id == run.id
