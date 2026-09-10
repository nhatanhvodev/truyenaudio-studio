from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.drafts import create_drafts_router
from app.contracts import ChapterState, ImportKind, RightsStatus, SourceType
from app.db.base import session_factory
from app.db.models import Chapter, Project, SourceRevision, SourceSegment
from app.settings.config import Settings


def _seed(migrated_engine: Engine) -> dict[str, str]:
    session = session_factory(migrated_engine)()
    try:
        project = Project(
            id="018f0000-0000-7000-8000-000000000801",
            title="T",
            slug="draft-api",
            source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
            rights_status=RightsStatus.PRIVATE_ONLY.value,
            default_language="zh-CN",
            target_language="vi-VN",
        )
        session.add(project)
        session.flush()
        chapter = Chapter(
            id="018f0000-0000-7000-8000-000000000802",
            project_id=project.id,
            ordinal=1,
            state=ChapterState.TRANSLATION_REVIEW.value,
        )
        session.add(chapter)
        session.flush()
        revision = SourceRevision(
            id="018f0000-0000-7000-8000-000000000803",
            chapter_id=chapter.id,
            revision_no=1,
            import_kind=ImportKind.PASTE.value,
            normalized_text="一",
            normalized_sha256=hashlib.sha256("一".encode("utf-8")).hexdigest(),
            han_char_count=0,
            total_char_count=1,
            normalizer_version="nfc-v1",
        )
        session.add(revision)
        session.flush()
        chapter.active_source_revision_id = revision.id
        segment = SourceSegment(
            id="018f0000-0000-7000-8000-000000000804",
            source_revision_id=revision.id,
            segment_index=0,
            paragraph_start=0,
            paragraph_end=0,
            source_text="一",
            source_sha256=hashlib.sha256("一".encode("utf-8")).hexdigest(),
            segment_kind="SOURCE",
        )
        session.add(segment)
        session.commit()
        return {
            "chapter_id": chapter.id,
            "revision_id": revision.id,
            "segment_id": segment.id,
        }
    finally:
        session.close()


def test_chapter_draft_create_restore_and_conflict(migrated_engine: Engine) -> None:
    seed = _seed(migrated_engine)
    db_path = Path(migrated_engine.url.database)
    app = FastAPI()
    app.include_router(create_drafts_router(Settings(data_root=db_path.parent)))

    with TestClient(app) as client:
        empty = client.get(
            f"/api/chapters/{seed['chapter_id']}/draft",
            params={"baseRevisionId": seed["revision_id"]},
        )
        assert empty.status_code == 200
        assert empty.json()["draft"] is None

        created = client.put(
            f"/api/chapters/{seed['chapter_id']}/draft",
            json={
                "base_revision_id": seed["revision_id"],
                "content": {seed["segment_id"]: "Nháp một."},
                "expected_revision": None,
            },
        )
        assert created.status_code == 200
        assert created.json()["draft"]["revision"] == 1

        restored = client.get(
            f"/api/chapters/{seed['chapter_id']}/draft",
            params={"baseRevisionId": seed["revision_id"]},
        )
        assert restored.json()["draft"]["content"] == {seed["segment_id"]: "Nháp một."}

        conflict = client.put(
            f"/api/chapters/{seed['chapter_id']}/draft",
            json={
                "base_revision_id": seed["revision_id"],
                "content": {seed["segment_id"]: "Ghi đè."},
                "expected_revision": 7,
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "DRAFT_REVISION_CONFLICT"

        invalid = client.put(
            f"/api/chapters/{seed['chapter_id']}/draft",
            json={
                "base_revision_id": seed["revision_id"],
                "content": {"unknown-segment": "x"},
                "expected_revision": 1,
            },
        )
        assert invalid.status_code == 400
        assert invalid.json()["detail"] == "DRAFT_SEGMENT_UNKNOWN"
