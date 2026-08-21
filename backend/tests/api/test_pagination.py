from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.contracts import ChapterState, ImportKind, RightsStatus, SourceType
from app.db.models import Chapter, Project, SourceRevision
from app.main import create_app
from app.settings.config import Settings


LOOPBACK_ORIGIN = "http://127.0.0.1:8765"


def test_chapter_page_has_no_full_text(settings: Settings, db_session: Session) -> None:
    project = _project_with_chapters(db_session)
    client = TestClient(create_app(settings=settings, acquire_lock=False), base_url=LOOPBACK_ORIGIN)

    body = client.get(f"/api/projects/{project.id}/chapters?limit=25").json()

    assert len(body["items"]) == 25
    assert body["nextCursor"]
    assert body["total"] == 50
    assert all("sourceText" not in item and "targetText" not in item for item in body["items"])


def test_chapter_page_cursor_returns_next_stable_page(settings: Settings, db_session: Session) -> None:
    project = _project_with_chapters(db_session)
    client = TestClient(create_app(settings=settings, acquire_lock=False), base_url=LOOPBACK_ORIGIN)

    first = client.get(f"/api/projects/{project.id}/chapters?limit=25").json()
    second = client.get(f"/api/projects/{project.id}/chapters?limit=25&cursor={first['nextCursor']}").json()

    assert [item["ordinal"] for item in first["items"]] == list(range(1, 26))
    assert [item["ordinal"] for item in second["items"]] == list(range(26, 51))
    assert second["nextCursor"] is None


def test_chapter_page_rejects_tampered_cursor(settings: Settings, db_session: Session) -> None:
    project = _project_with_chapters(db_session)
    client = TestClient(create_app(settings=settings, acquire_lock=False), base_url=LOOPBACK_ORIGIN)

    response = client.get(f"/api/projects/{project.id}/chapters?cursor=tampered")

    assert response.status_code == 400
    assert response.json()["detail"] == "CHAPTER_CURSOR_INVALID"


def _project_with_chapters(db_session: Session) -> Project:
    project = Project(
        id="018f0000-0000-7000-8000-000000003000",
        title="Paged Project",
        slug="paged-project",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
    )
    db_session.add(project)
    db_session.flush()
    for ordinal in range(1, 51):
        chapter = Chapter(
            id=f"018f0000-0000-7000-8000-000000003{ordinal:03d}",
            project_id=project.id,
            ordinal=ordinal,
            source_title=f"Chapter {ordinal}",
            state=ChapterState.NORMALIZED.value,
        )
        db_session.add(chapter)
        db_session.flush()
        revision = SourceRevision(
            id=f"018f0000-0000-7000-8000-000000004{ordinal:03d}",
            chapter_id=chapter.id,
            revision_no=1,
            import_kind=ImportKind.PASTE.value,
            normalized_text=f"Source text {ordinal}",
            normalized_sha256=f"{ordinal:064x}",
            han_char_count=1,
            total_char_count=12,
            normalizer_version="test",
        )
        db_session.add(revision)
        db_session.flush()
        chapter.active_source_revision_id = revision.id
    db_session.commit()
    return project
