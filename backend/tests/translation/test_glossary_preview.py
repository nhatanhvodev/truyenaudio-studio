from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.glossary import create_glossary_router
from app.contracts import (
    ChapterState,
    ImportKind,
    RightsStatus,
    RunStatus,
    SourceType,
)
from app.db.base import create_engine_for, session_factory
from app.db.models import (
    Chapter,
    GlossaryEntry,
    Project,
    SourceRevision,
    SourceSegment,
    TranslationRun,
    TranslationSegment,
)
from app.modules.translation.glossary import GlossaryCommand, GlossaryService
from app.settings.config import Settings


def test_preview_persists_nothing(db_session) -> None:
    project = _project(db_session, "preview-no-write")
    _chapter_with_source(
        db_session, project.id, ordinal=1, text="林动抬头。", base=1, with_run=True
    )
    service = GlossaryService(db_session)

    before = _row_counts(db_session)
    result = service.preview(
        project.id, GlossaryCommand("林动", "Lâm Động", is_locked=True)
    )
    after = _row_counts(db_session)
    db_session.commit()
    committed = _row_counts(db_session)

    assert result.changed is True
    assert result.affected_count == 1
    assert before == after == committed


def test_preview_then_upsert_matches_preview_everywhere(db_session) -> None:
    project = _project(db_session, "preview-parity")
    _chapter_with_source(
        db_session, project.id, ordinal=1, text="林动抬头。", base=10, with_run=True
    )
    _chapter_with_source(
        db_session, project.id, ordinal=2, text="林动笑了。", base=20, with_run=True
    )
    service = GlossaryService(db_session)

    preview_v1 = service.preview(
        project.id, GlossaryCommand("林动", "Lam Dong", is_locked=True)
    )
    upsert_v1 = service.upsert(
        project.id, GlossaryCommand("林动", "Lam Dong", is_locked=True)
    )
    preview_v2 = service.preview(
        project.id,
        GlossaryCommand("林动", "Lâm Động", is_locked=True, description="v2"),
    )
    upsert_v2 = service.upsert(
        project.id,
        GlossaryCommand("林动", "Lâm Động", is_locked=True, description="v2"),
    )

    for preview, upsert in ((preview_v1, upsert_v1), (preview_v2, upsert_v2)):
        assert upsert.revision.sha256 == preview.revision_preview_sha256
        assert upsert.affected_source_segment_ids == tuple(
            segment.segment_id for segment in preview.affected_segments
        )
        assert upsert.invalidated == preview.invalidated_runs
    # The first real upsert superseded both approved runs, so the second
    # preview correctly finds nothing left to invalidate.
    assert preview_v1.invalidated_runs != ()
    assert preview_v2.invalidated_runs == ()


def test_preview_scope_excludes_out_of_scope_chapters(db_session) -> None:
    project = _project(db_session, "preview-scope")
    chapter_1 = _chapter_with_source(
        db_session, project.id, ordinal=1, text="林动抬头。", base=10, with_run=True
    )
    chapter_2 = _chapter_with_source(
        db_session, project.id, ordinal=2, text="林动笑了。", base=20, with_run=True
    )
    service = GlossaryService(db_session)

    result = service.preview(
        project.id,
        GlossaryCommand(
            "林动", "Lâm Động", is_locked=True, scope_from_ordinal=1, scope_to_ordinal=1
        ),
    )

    assert [segment.ordinal for segment in result.affected_segments] == [1]
    assert result.affected_count == 1
    assert result.invalidated_runs == (chapter_1["run_id"],)
    assert chapter_2["run_id"] not in result.invalidated_runs


def test_preview_reports_scope_empty_when_term_absent(db_session) -> None:
    project = _project(db_session, "preview-empty")
    _chapter_with_source(
        db_session, project.id, ordinal=1, text="Không có thuật ngữ.", base=10
    )
    service = GlossaryService(db_session)

    result = service.preview(
        project.id, GlossaryCommand("林动", "Lâm Động", is_locked=True)
    )

    assert result.changed is True
    assert result.revision_preview_sha256
    assert result.affected_count == 0
    assert "SCOPE_EMPTY" in result.warnings
    assert db_session.scalar(select(func.count(GlossaryEntry.id))) == 0


def test_preview_warns_ordinal_out_of_range(db_session) -> None:
    project = _project(db_session, "preview-range")
    _chapter_with_source(
        db_session, project.id, ordinal=1, text="林动抬头。", base=10
    )
    _chapter_with_source(db_session, project.id, ordinal=2, text="Hết chương.", base=20)
    service = GlossaryService(db_session)

    result = service.preview(
        project.id,
        GlossaryCommand("林动", "Lâm Động", is_locked=True, scope_from_ordinal=5),
    )

    assert "ORDINAL_OUT_OF_RANGE" in result.warnings
    assert "SCOPE_EMPTY" in result.warnings
    assert result.affected_count == 0


def test_preview_warns_term_conflict_with_other_entry(db_session) -> None:
    project = _project(db_session, "preview-conflict")
    service = GlossaryService(db_session)
    service.upsert(project.id, GlossaryCommand("林动", "Tên A", is_locked=True))
    service.upsert(project.id, GlossaryCommand("小炎", "Tên B", is_locked=True))

    result = service.preview(project.id, GlossaryCommand("林动", "Tên B"))

    assert "TERM_CONFLICT" in result.warnings
    active = service.upsert(project.id, GlossaryCommand("林动", "Tên C"))
    views = {view.source_term: view for view in active.revision.entries}
    assert views["林动"].target_term == "Tên C"
    assert views["小炎"].target_term == "Tên B"


def test_preview_noop_reports_no_op_without_invalidation(db_session) -> None:
    project = _project(db_session, "preview-noop")
    chapter_1 = _chapter_with_source(
        db_session, project.id, ordinal=1, text="林动抬头。", base=10, with_run=True
    )
    service = GlossaryService(db_session)
    service.upsert(
        project.id, GlossaryCommand("林动", "Lâm Động", is_locked=True)
    )
    runs_before = db_session.get(TranslationRun, chapter_1["run_id"]).status

    result = service.preview(
        project.id, GlossaryCommand("林动", "Lâm Động", is_locked=True)
    )

    assert result.changed is False
    assert "NO_OP" in result.warnings
    assert "TERM_CONFLICT" not in result.warnings
    assert result.invalidated_count == 0
    assert result.affected_count == 1
    assert db_session.get(TranslationRun, chapter_1["run_id"]).status == runs_before


def test_preview_chapter_1200_truncates_excerpt_and_stays_cheap(db_session) -> None:
    project = _project(db_session, "preview-1200")
    filler = "Câu văn dài không liên quan. " * 20000
    _chapter_with_source(
        db_session, project.id, ordinal=1200, text="林动 xuất hiện. " + filler, base=10
    )
    service = GlossaryService(db_session)

    result = service.preview(
        project.id,
        GlossaryCommand("林动", "Lâm Động", is_locked=True, scope_from_ordinal=1200),
    )
    beyond = service.preview(
        project.id,
        GlossaryCommand("林动", "Lâm Động", is_locked=True, scope_from_ordinal=1201),
    )

    assert result.affected_count == 1
    assert len(result.affected_segments[0].excerpt) <= 120
    assert "林动" in result.affected_segments[0].excerpt
    assert "ORDINAL_OUT_OF_RANGE" not in result.warnings
    assert beyond.affected_count == 0
    assert "ORDINAL_OUT_OF_RANGE" in beyond.warnings
    assert "SCOPE_EMPTY" in beyond.warnings


def test_preview_api_roundtrip_then_old_route_still_upserts(tmp_path) -> None:
    db_path = tmp_path / "studio.sqlite3"
    project_id = _migrated_project_with_segment(db_path, tmp_path)

    app = FastAPI()
    app.include_router(create_glossary_router(Settings(data_root=tmp_path)))

    with TestClient(app) as client:
        preview = client.post(
            f"/api/projects/{project_id}/glossary/preview",
            json={
                "source_term": "林动",
                "target_term": "Lâm Động",
                "is_locked": True,
            },
        )
        assert preview.status_code == 200
        body = preview.json()
        assert set(body) == {
            "revisionPreviewSha256",
            "changed",
            "affectedSegments",
            "affectedCount",
            "invalidatedRuns",
            "invalidatedCount",
            "warnings",
        }
        assert body["changed"] is True
        assert body["affectedCount"] == 1
        assert "林动" in body["affectedSegments"][0]["excerpt"]
        assert body["warnings"] == []

        upsert = client.post(
            f"/api/projects/{project_id}/glossary",
            json={
                "source_term": "林动",
                "target_term": "Lâm Động",
                "is_locked": True,
            },
        )
        assert upsert.status_code == 200
        assert upsert.json()["revision"]["entries"][0]["target_term"] == "Lâm Động"

        listing = client.get(f"/api/projects/{project_id}/glossary")
        assert listing.status_code == 200
        assert len(listing.json()["revision"]["entries"]) == 1

    engine = create_engine_for(db_path)
    with session_factory(engine)() as session:
        assert session.scalar(select(func.count(GlossaryEntry.id))) == 1
    engine.dispose()


def test_preview_api_unknown_project_is_404(tmp_path) -> None:
    _migrated_project_with_segment(tmp_path / "studio.sqlite3", tmp_path)

    app = FastAPI()
    app.include_router(create_glossary_router(Settings(data_root=tmp_path)))

    with TestClient(app) as client:
        response = client.post(
            "/api/projects/018f0000-0000-7000-8000-00000000dead/glossary/preview",
            json={"source_term": "林动", "target_term": "Lâm Động"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "PROJECT_NOT_FOUND"


def _row_counts(db_session) -> dict[str, int]:
    return {
        "glossary_entries": db_session.scalar(select(func.count(GlossaryEntry.id))) or 0,
        "translation_segments": db_session.scalar(
            select(func.count(TranslationSegment.__table__.c.id))
        )
        or 0,
        "source_segments": db_session.scalar(
            select(func.count(SourceSegment.id))
        )
        or 0,
        "translation_runs": db_session.scalar(select(func.count(TranslationRun.id))) or 0,
    }


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


def _chapter_with_source(
    db_session,
    project_id: str,
    *,
    ordinal: int,
    text: str,
    base: int,
    with_run: bool = False,
) -> dict[str, str]:
    chapter = Chapter(
        id=_rid(base + 1),
        project_id=project_id,
        ordinal=ordinal,
        source_title=f"Chương {ordinal}",
        state=(
            ChapterState.TRANSLATION_APPROVED.value
            if with_run
            else ChapterState.NORMALIZED.value
        ),
    )
    db_session.add(chapter)
    revision = SourceRevision(
        id=_rid(base + 2),
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text=text,
        normalized_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        han_char_count=0,
        total_char_count=len(text),
        normalizer_version="nfc-v1",
    )
    db_session.add(revision)
    db_session.flush()
    chapter.active_source_revision_id = revision.id
    segment = SourceSegment(
        id=_rid(base + 3),
        source_revision_id=revision.id,
        segment_index=0,
        paragraph_start=0,
        paragraph_end=0,
        source_text=text,
        source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        segment_kind="SOURCE",
    )
    db_session.add(segment)
    result = {"chapter_id": chapter.id, "segment_id": segment.id, "run_id": ""}
    if with_run:
        run = TranslationRun(
            id=_rid(base + 4),
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
                id=_rid(base + 5),
                translation_run_id=run.id,
                source_segment_id=segment.id,
                target_text="Ban dich.",
                target_sha256=hashlib.sha256("Ban dich.".encode("utf-8")).hexdigest(),
                was_cache_hit=False,
                manually_edited=False,
            )
        )
        chapter.approved_translation_run_id = run.id
        result["run_id"] = run.id
    db_session.flush()
    return result


def _migrated_project_with_segment(db_path: Path, tmp_path: Path) -> str:
    from alembic import command
    from alembic.config import Config

    backend_root = Path(__file__).parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option(
        "sqlalchemy.url", f"sqlite:///{db_path.as_posix()}"
    )
    command.upgrade(config, "head")

    engine = create_engine_for(db_path)
    with session_factory(engine)() as session:
        project = _project(session, "preview-api")
        _chapter_with_source(
            session, project.id, ordinal=1, text="林动抬头。", base=10, with_run=True
        )
        session.commit()
        project_id = project.id
    engine.dispose()
    return project_id


def _rid(n: int) -> str:
    return f"018f0000-0000-7000-8000-{n:012x}"