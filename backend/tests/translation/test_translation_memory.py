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
from app.modules.translation.translation_memory import (
    count_for_project,
    exact_match,
    record_approved_run,
)


def test_record_requires_approved_run(db_session) -> None:
    fixture = _approved_fixture(db_session, status=RunStatus.REVIEW.value)

    with pytest.raises(ValueError, match="TRANSLATION_RUN_NOT_APPROVED"):
        record_approved_run(db_session, fixture["run_id"], id_factory=_ids())


def test_exact_match_reuses_only_same_fingerprint(db_session) -> None:
    fixture = _approved_fixture(db_session)
    glossary_hash = fixture["glossary_hash"]
    project = db_session.get(Project, fixture["project_id"])

    assert record_approved_run(db_session, fixture["run_id"], id_factory=_ids()) == 1

    match = exact_match(
        db_session,
        project_id=fixture["project_id"],
        source_text="林动抬头。",
        source_language=project.default_language,
        target_language=project.target_language,
        glossary_hash=glossary_hash,
    )
    assert match is not None
    assert match.target_text == "Bản dịch đã duyệt."

    other_glossary = "f" * 64
    assert (
        exact_match(
            db_session,
            project_id=fixture["project_id"],
            source_text="林动抬头。",
            source_language=project.default_language,
            target_language=project.target_language,
            glossary_hash=other_glossary,
        )
        is None
    )
    assert (
        exact_match(
            db_session,
            project_id="018f0000-0000-7000-8000-000000000999",
            source_text="林动抬头。",
            source_language=project.default_language,
            target_language=project.target_language,
            glossary_hash=glossary_hash,
        )
        is None
    )


def test_newer_approved_run_replaces_row_for_same_fingerprint(db_session) -> None:
    fixture = _approved_fixture(db_session, target_text="Bản dịch đã duyệt.")
    project = db_session.get(Project, fixture["project_id"])
    service_run = TranslationRun(
        id="018f0000-0000-7000-8000-000000000401",
        chapter_id=fixture["chapter_id"],
        source_revision_id=fixture["revision_id"],
        prompt_version="translation-v1",
        glossary_revision_hash=fixture["glossary_hash"],
        status=RunStatus.APPROVED.value,
        translation_text_sha256="b" * 64,
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    db_session.add(service_run)
    db_session.flush()
    db_session.add(
        TranslationSegment(
            id="018f0000-0000-7000-8000-000000000402",
            translation_run_id=service_run.id,
            source_segment_id=fixture["segment_id"],
            target_text="Bản dịch mới hơn.",
            target_sha256=hashlib.sha256("Bản dịch mới hơn.".encode("utf-8")).hexdigest(),
            was_cache_hit=False,
            manually_edited=True,
        )
    )
    db_session.flush()

    record_approved_run(db_session, fixture["run_id"], id_factory=_ids())
    record_approved_run(db_session, service_run.id, id_factory=_ids())

    assert count_for_project(db_session, fixture["project_id"]) == 1
    match = exact_match(
        db_session,
        project_id=fixture["project_id"],
        source_text="林动抬头。",
        source_language=project.default_language,
        target_language=project.target_language,
        glossary_hash=fixture["glossary_hash"],
    )
    assert match is not None
    assert match.target_text == "Bản dịch mới hơn."


def test_superseded_approving_run_is_not_reused(db_session) -> None:
    fixture = _approved_fixture(db_session)
    project = db_session.get(Project, fixture["project_id"])
    record_approved_run(db_session, fixture["run_id"], id_factory=_ids())

    run = db_session.get(TranslationRun, fixture["run_id"])
    run.status = RunStatus.SUPERSEDED.value
    db_session.flush()

    assert (
        exact_match(
            db_session,
            project_id=fixture["project_id"],
            source_text="林动抬头。",
            source_language=project.default_language,
            target_language=project.target_language,
            glossary_hash=fixture["glossary_hash"],
        )
        is None
    )


def _approved_fixture(
    db_session,
    *,
    status: str = RunStatus.APPROVED.value,
    target_text: str = "Bản dịch đã duyệt.",
) -> dict[str, str]:
    project = Project(
        id="018f0000-0000-7000-8000-000000000101",
        title="Truyen",
        slug="tm-fixture",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
        default_language="zh-CN",
        target_language="vi-VN",
    )
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(
        id="018f0000-0000-7000-8000-000000000102",
        project_id=project.id,
        ordinal=1,
        state=ChapterState.TRANSLATION_APPROVED.value,
    )
    db_session.add(chapter)
    db_session.flush()
    revision = SourceRevision(
        id="018f0000-0000-7000-8000-000000000103",
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
        id="018f0000-0000-7000-8000-000000000104",
        source_revision_id=revision.id,
        segment_index=0,
        paragraph_start=0,
        paragraph_end=0,
        source_text="林动抬头。",
        source_sha256=hashlib.sha256("林动抬头。".encode("utf-8")).hexdigest(),
        segment_kind="SOURCE",
    )
    db_session.add(segment)
    glossary_hash = "e" * 64
    run = TranslationRun(
        id="018f0000-0000-7000-8000-000000000105",
        chapter_id=chapter.id,
        source_revision_id=revision.id,
        prompt_version="translation-v1",
        glossary_revision_hash=glossary_hash,
        status=status,
        translation_text_sha256="a" * 64,
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        TranslationSegment(
            id="018f0000-0000-7000-8000-000000000106",
            translation_run_id=run.id,
            source_segment_id=segment.id,
            target_text=target_text,
            target_sha256=hashlib.sha256(target_text.encode("utf-8")).hexdigest(),
            was_cache_hit=False,
            manually_edited=False,
        )
    )
    db_session.flush()
    return {
        "project_id": project.id,
        "chapter_id": chapter.id,
        "revision_id": revision.id,
        "segment_id": segment.id,
        "run_id": run.id,
        "glossary_hash": glossary_hash,
    }


def _ids():
    counter = 0

    def factory() -> str:
        nonlocal counter
        counter += 1
        return f"018f0000-0000-7000-8000-{counter:012x}"

    return factory
