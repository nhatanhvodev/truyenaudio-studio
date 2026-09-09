from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.characters import create_characters_router
from app.contracts import ChapterState, ImportKind, RightsStatus, SourceType
from app.db.base import create_engine_for, session_factory
from app.db.models import Chapter, Project, SourceRevision, SourceSegment
from app.settings.config import Settings


def test_characters_api_candidate_approve_resolve_and_relationship(tmp_path) -> None:
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
        project_id, revision_id, segment_id = _seed(session)
    engine.dispose()

    app = FastAPI()
    app.include_router(create_characters_router(Settings(data_root=tmp_path)))

    with TestClient(app) as client:
        created = client.post(
            f"/api/projects/{project_id}/characters",
            json={"canonical_name": "林动", "entity_type": "PERSON", "aliases": ["Lâm Động"], "role": "protagonist"},
        )
        assert created.status_code == 200
        character_id = created.json()["character"]["character_id"]
        assert created.json()["character"]["status"] == "CANDIDATE"

        approved = client.post(
            f"/api/projects/{project_id}/characters/{character_id}/approve",
            json={
                "evidence_source_revision_id": revision_id,
                "evidence_segment_ids": [segment_id],
            },
        )
        assert approved.status_code == 200
        assert approved.json()["character"]["status"] == "APPROVED"

        resolved = client.get(
            f"/api/projects/{project_id}/characters/resolve",
            params={"alias": "Lâm Động"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["character"]["character_id"] == character_id

        relationship = client.post(
            f"/api/projects/{project_id}/characters/relationships",
            json={
                "from_character_id": character_id,
                "to_character_id": character_id,
                "from_ordinal": 1,
                "to_ordinal": None,
                "addressing": {"self": "ta"},
            },
        )
        assert relationship.status_code == 400  # self-relationship rejected
        assert relationship.json()["detail"] == "CHARACTER_RELATIONSHIP_SELF"

        listed = client.get(f"/api/projects/{project_id}/characters")
        assert listed.status_code == 200
        assert len(listed.json()["characters"]) == 1


def _seed(session) -> tuple[str, str, str]:
    project = Project(
        id="018f0000-0000-7000-8000-000000000201",
        title="Truyen",
        slug="char-api",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
        default_language="zh-CN",
        target_language="vi-VN",
    )
    session.add(project)
    session.flush()
    chapter = Chapter(
        id="018f0000-0000-7000-8000-000000000202",
        project_id=project.id,
        ordinal=1,
        state=ChapterState.NORMALIZED.value,
    )
    session.add(chapter)
    session.flush()
    text = "林动抬头。"
    revision = SourceRevision(
        id="018f0000-0000-7000-8000-000000000203",
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text=text,
        normalized_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        han_char_count=0,
        total_char_count=5,
        normalizer_version="nfc-v1",
    )
    session.add(revision)
    session.flush()
    chapter.active_source_revision_id = revision.id
    segment = SourceSegment(
        id="018f0000-0000-7000-8000-000000000204",
        source_revision_id=revision.id,
        segment_index=0,
        paragraph_start=0,
        paragraph_end=0,
        source_text=text,
        source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        segment_kind="SOURCE",
    )
    session.add(segment)
    session.commit()
    return project.id, revision.id, segment.id
