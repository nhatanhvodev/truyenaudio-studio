from __future__ import annotations

import hashlib

import pytest

from app.contracts import ChapterState, ImportKind, QaCategory, QaSeverity, RightsStatus, RunStatus, SourceType
from app.db.models import Chapter, GlossaryEntry, Project, SourceRevision, SourceSegment, TranslationRun, TranslationSegment
from app.modules.translation.glossary import (
    GlossaryCommand,
    GlossaryService,
    locked_rules_for_chapter,
)
from app.modules.translation.qa import run_deterministic_qa


def test_glossary_upsert_persists_description_evidence_forbidden_and_scope(db_session) -> None:
    project = _project(db_session, slug="c02-fields")
    service = GlossaryService(db_session, id_factory=_ids())

    result = service.upsert(
        project.id,
        GlossaryCommand(
            "林动",
            "Lâm Động",
            is_locked=True,
            description="Nhân vật chính.",
            forbidden_forms=("Lam Dong", " lam dong ", "Lam Dong"),
            evidence="Chương 1 bản gốc",
            scope_from_ordinal=1,
            scope_to_ordinal=5,
        ),
    )

    view = result.revision.entries[0]
    assert view.description == "Nhân vật chính."
    assert view.forbidden_forms == ("Lam Dong", "lam dong")
    assert view.evidence == "Chương 1 bản gốc"
    assert view.scope_from_ordinal == 1
    assert view.scope_to_ordinal == 5
    row = db_session.get(GlossaryEntry, view.id)
    assert row is not None
    assert row.forbidden_forms == ["Lam Dong", "lam dong"]


def test_glossary_scope_order_and_bounds_are_validated(db_session) -> None:
    project = _project(db_session, slug="c02-scope-validate")
    service = GlossaryService(db_session, id_factory=_ids())

    with pytest.raises(ValueError, match="GLOSSARY_SCOPE_INVALID"):
        service.upsert(
            project.id,
            GlossaryCommand("林动", "Lâm Động", scope_from_ordinal=7, scope_to_ordinal=3),
        )
    with pytest.raises(ValueError, match="GLOSSARY_SCOPE_INVALID"):
        service.upsert(
            project.id,
            GlossaryCommand("林动", "Lâm Động", scope_from_ordinal=0),
        )


def test_glossary_rejects_internal_conflict_target_in_forbidden_forms(db_session) -> None:
    project = _project(db_session, slug="c02-conflict")
    service = GlossaryService(db_session, id_factory=_ids())

    with pytest.raises(ValueError, match="GLOSSARY_CONFLICT_INTERNAL"):
        service.upsert(
            project.id,
            GlossaryCommand(
                "林动",
                "Lam Dong",
                is_locked=True,
                forbidden_forms=("Lam Dong",),
            ),
        )


def test_glossary_semantic_edit_creates_revision_but_noop_does_not(db_session) -> None:
    project = _project(db_session, slug="c02-revisions")
    service = GlossaryService(db_session, id_factory=_ids())

    first = service.upsert(
        project.id,
        GlossaryCommand(
            "林动",
            "Lâm Động",
            is_locked=True,
            description="v1",
            scope_from_ordinal=1,
            scope_to_ordinal=3,
        ),
    )
    noop = service.upsert(
        project.id,
        GlossaryCommand(
            "林动",
            "Lâm Động",
            is_locked=True,
            description="v1",
            scope_from_ordinal=1,
            scope_to_ordinal=3,
        ),
    )
    second = service.upsert(
        project.id,
        GlossaryCommand(
            "林动",
            "Lâm Động",
            is_locked=True,
            description="v2",
            scope_from_ordinal=1,
            scope_to_ordinal=5,
        ),
    )

    rows = db_session.query(GlossaryEntry).order_by(GlossaryEntry.revision_no).all()
    assert len(rows) == 2
    assert rows[1].supersedes_id == rows[0].id
    assert noop.revision.sha256 == first.revision.sha256
    assert second.revision.sha256 != first.revision.sha256


def test_locked_rules_respect_chapter_scope(db_session) -> None:
    project = _project(db_session, slug="c02-scope-apply")
    service = GlossaryService(db_session, id_factory=_ids())
    service.upsert(
        project.id,
        GlossaryCommand(
            "林动",
            "Lâm Động",
            is_locked=True,
            forbidden_forms=("Lam Dong",),
            scope_from_ordinal=1,
            scope_to_ordinal=3,
        ),
    )
    service.upsert(
        project.id,
        GlossaryCommand("门", "cửa", is_locked=True),
    )

    chapter_2 = locked_rules_for_chapter(db_session, project.id, 2)
    chapter_5 = locked_rules_for_chapter(db_session, project.id, 5)

    assert {rule.source_term for rule in chapter_2} == {"林动", "门"}
    assert {rule.source_term for rule in chapter_5} == {"门"}
    by_term = {rule.source_term: rule for rule in chapter_2}
    assert by_term["林动"].forbidden_forms == ("Lam Dong",)


def test_unlocked_entry_never_produces_a_rule(db_session) -> None:
    project = _project(db_session, slug="c02-not-locked")
    service = GlossaryService(db_session, id_factory=_ids())
    service.upsert(project.id, GlossaryCommand("林动", "Lâm Động", is_locked=False))

    assert locked_rules_for_chapter(db_session, project.id, 1) == ()


def test_deterministic_qa_flags_forbidden_glossary_forms() -> None:
    issues = run_deterministic_qa(
        "林动抬头。",
        "Lam Dong ngẩng đầu.",
        locked_terms=(("林动", "Lâm Động"),),
        forbidden_forms=(("林动", ("Lam Dong",)),),
    )

    assert any(
        issue.category is QaCategory.NAME
        and issue.severity is QaSeverity.MAJOR
        and issue.rule_or_model == "deterministic-qa-v1:forbidden-form"
        for issue in issues
    )


def test_deterministic_qa_ignores_forbidden_form_when_source_term_absent() -> None:
    issues = run_deterministic_qa(
        "Cô ấy mở cửa.",
        "Co ay mo cua.",
        forbidden_forms=(("林动", ("Lam Dong",)),),
    )

    assert not any(issue.rule_or_model == "deterministic-qa-v1:forbidden-form" for issue in issues)


def test_glossary_edit_supersedes_approved_run_only_in_scoped_chapter(db_session) -> None:
    project = _project(db_session, slug="c02-stale")
    chapter_1 = _chapter_with_approved_run(db_session, project.id, ordinal=1, source_text="林动抬头。", run_suffix="1")
    chapter_2 = _chapter_with_approved_run(db_session, project.id, ordinal=2, source_text="林动笑了。", run_suffix="2")
    service = GlossaryService(db_session, id_factory=_ids())

    result = service.upsert(
        project.id,
        GlossaryCommand(
            "林动",
            "Lâm Động",
            is_locked=True,
            scope_from_ordinal=1,
            scope_to_ordinal=1,
        ),
    )

    assert result.invalidated == (chapter_1["run_id"],)
    db_session.expire_all()
    assert db_session.get(TranslationRun, chapter_1["run_id"]).status == RunStatus.SUPERSEDED.value
    assert db_session.get(Chapter, chapter_1["chapter_id"]).approved_translation_run_id is None
    assert db_session.get(TranslationRun, chapter_2["run_id"]).status == RunStatus.APPROVED.value
    assert db_session.get(Chapter, chapter_2["chapter_id"]).approved_translation_run_id == chapter_2["run_id"]


def _chapter_with_approved_run(
    db_session,
    project_id: str,
    *,
    ordinal: int,
    source_text: str,
    run_suffix: str,
) -> dict[str, str]:
    chapter = Chapter(
        id=f"018f0000-0000-7000-8000-{run_suffix}00000001",
        project_id=project_id,
        ordinal=ordinal,
        state=ChapterState.TRANSLATION_APPROVED.value,
    )
    db_session.add(chapter)
    db_session.flush()
    revision = SourceRevision(
        id=f"018f0000-0000-7000-8000-{run_suffix}00000002",
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text=source_text,
        normalized_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        han_char_count=0,
        total_char_count=len(source_text),
        normalizer_version="nfc-v1",
    )
    db_session.add(revision)
    db_session.flush()
    chapter.active_source_revision_id = revision.id
    segment = SourceSegment(
        id=f"018f0000-0000-7000-8000-{run_suffix}00000003",
        source_revision_id=revision.id,
        segment_index=0,
        paragraph_start=0,
        paragraph_end=0,
        source_text=source_text,
        source_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        segment_kind="SOURCE",
    )
    db_session.add(segment)
    run = TranslationRun(
        id=f"018f0000-0000-7000-8000-{run_suffix}00000004",
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
    db_session.add(
        TranslationSegment(
            id=f"018f0000-0000-7000-8000-{run_suffix}00000005",
            translation_run_id=run.id,
            source_segment_id=segment.id,
            target_text="Ban dich.",
            target_sha256=hashlib.sha256("Ban dich.".encode("utf-8")).hexdigest(),
            was_cache_hit=False,
            manually_edited=False,
        )
    )
    chapter.approved_translation_run_id = run.id
    db_session.flush()
    return {"chapter_id": chapter.id, "run_id": run.id}


def _project(db_session, slug: str) -> Project:
    project = Project(
        id="018f0000-0000-7000-8000-000000000101",
        title="Truyen",
        slug=slug,
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
        default_language="zh-CN",
        target_language="vi-VN",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _ids():
    counter = 0

    def factory() -> str:
        nonlocal counter
        counter += 1
        return f"018f0000-0000-7000-8000-{counter:012x}"

    return factory
