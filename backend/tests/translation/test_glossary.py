from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.glossary import create_glossary_router
from app.contracts import ChapterState, ImportKind, RightsStatus, SourceType
from app.db.base import create_engine_for, session_factory
from app.db.models import Chapter, GlossaryEntry, Project, SourceRevision, SourceSegment
from app.modules.translation.glossary import (
    GlossaryCommand,
    GlossaryService,
    active_glossary,
)
from app.settings.config import Settings


def test_active_glossary_uses_canonical_sha256_json(db_session) -> None:
    project = _project(db_session)
    service = GlossaryService(db_session, id_factory=_ids())

    service.upsert(
        project.id,
        GlossaryCommand("林动", "Lâm Động", "lâm động", "NAME", None, "", True),
    )
    revision = active_glossary(db_session, project.id)

    payload = [
        {
            "addressing_notes": "",
            "category": "NAME",
            "description": None,
            "evidence": None,
            "forbidden_forms": None,
            "gender": None,
            "id": revision.entries[0].id,
            "is_locked": True,
            "reading": "lâm động",
            "revision_no": 1,
            "scope_from_ordinal": None,
            "scope_to_ordinal": None,
            "source_term": "林动",
            "supersedes_id": None,
            "target_term": "Lâm Động",
        }
    ]
    expected = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    assert revision.sha256 == expected


def test_locked_glossary_change_reports_affected_source_segments(db_session) -> None:
    project = _project(db_session)
    segment = _segment(db_session, project.id, "林动抬头。")

    result = GlossaryService(db_session, id_factory=_ids()).upsert(
        project.id,
        GlossaryCommand("林动", "Lâm Động", "lâm động", "NAME", None, "", True),
    )

    assert result.revision.sha256
    assert result.affected_source_segment_ids == (segment.id,)


def test_glossary_update_creates_immutable_revision_and_supersedes_old_entry(
    db_session,
) -> None:
    project = _project(db_session)
    service = GlossaryService(db_session, id_factory=_ids())

    first = service.upsert(
        project.id, GlossaryCommand("林动", "Lam Dong", None, "NAME", None, None, False)
    )
    second = service.upsert(
        project.id,
        GlossaryCommand("林动", "Lâm Động", "lâm động", "NAME", "MALE", "", True),
    )

    rows = db_session.scalars(
        select(GlossaryEntry).order_by(GlossaryEntry.revision_no)
    ).all()
    assert len(rows) == 2
    assert rows[0].target_term == "Lam Dong"
    assert rows[1].target_term == "Lâm Động"
    assert rows[1].revision_no == 2
    assert rows[1].supersedes_id == rows[0].id
    assert first.revision.sha256 != second.revision.sha256
    assert [entry.id for entry in active_glossary(db_session, project.id).entries] == [
        rows[1].id
    ]


def test_glossary_api_upserts_and_returns_active_revision(tmp_path) -> None:
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
        project = _project(session)
        session.commit()
        project_id = project.id
    engine.dispose()

    app = FastAPI()
    app.include_router(create_glossary_router(Settings(data_root=tmp_path)))

    with TestClient(app) as client:
        response = client.post(
            f"/api/projects/{project_id}/glossary",
            json={
                "source_term": "林动",
                "target_term": "Lâm Động",
                "reading": "lâm động",
                "category": "NAME",
                "gender": None,
                "addressing_notes": "",
                "is_locked": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["revision"]["entries"][0]["source_term"] == "林动"
    assert response.json()["affected_source_segment_ids"] == []


def _project(db_session) -> Project:
    project = Project(
        id="018f0000-0000-7000-8000-000000000101",
        title="Truyen",
        slug="truyen",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.name,
        rights_status=RightsStatus.PRIVATE_ONLY.name,
    )
    db_session.add(project)
    db_session.flush()
    return project


def _segment(db_session, project_id: str, text: str) -> SourceSegment:
    chapter = Chapter(
        id="018f0000-0000-7000-8000-000000000201",
        project_id=project_id,
        ordinal=1,
        source_title="一",
        state=ChapterState.NORMALIZED.name,
    )
    db_session.add(chapter)
    revision = SourceRevision(
        id="018f0000-0000-7000-8000-000000000301",
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.name,
        normalized_text=text,
        normalized_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        han_char_count=len(text) - 1,
        total_char_count=len(text),
        normalizer_version="nfc-v1",
    )
    db_session.add(revision)
    db_session.flush()
    chapter.active_source_revision_id = revision.id
    segment = SourceSegment(
        id="018f0000-0000-7000-8000-000000000401",
        source_revision_id=revision.id,
        segment_index=0,
        paragraph_start=0,
        paragraph_end=0,
        source_text=text,
        source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        segment_kind="SOURCE",
    )
    db_session.add(segment)
    db_session.flush()
    return segment


def _ids():
    counter = 0

    def factory() -> str:
        nonlocal counter
        counter += 1
        return f"018f0000-0000-7000-8000-{counter:012x}"

    return factory
