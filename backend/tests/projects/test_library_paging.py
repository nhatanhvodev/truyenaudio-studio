"""U03: the library endpoint must page, filter and never mount a huge project.

Acceptance covered here:

- `GET /api/projects` is cursor-paginated with bounded `limit` (max 100) and page
  metadata (`limit`, `total`, `count`, `hasMore`, `nextCursor`);
- a project row carries at most 30 chapter summaries plus an exact `chapterCount`,
  so a project with thousands of chapters cannot pull its chapters (or any source
  text) into the payload;
- the page is built with a **constant number of statements** regardless of how
  many projects/chapters exist (no per-project query storm);
- a forged cursor is rejected with `LIBRARY_CURSOR_INVALID`, and title search
  filters server-side.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Engine, event

from app.api.projects import create_projects_router
from app.contracts import ChapterState, ImportKind, RightsStatus, SourceType
from app.db.base import session_factory
from app.db.models import Chapter, Project, SourceRevision, SourceSegment
from app.modules.projects.queries import (
    DEFAULT_PROJECT_LIMIT,
    MAX_PROJECT_LIMIT,
    MOUNTED_CHAPTERS_PER_PROJECT,
    InvalidCursor,
    ProjectQueries,
)
from app.settings.config import Settings

SOURCE_TEXT = "NỘI DUNG NGUỒN KHÔNG ĐƯỢC LỘ TRONG THƯ VIỆN"


def _seed_project(session, index: int, *, chapters: int, project_id: str | None = None) -> str:
    pid = project_id or f"018f0000-0000-7000-8000-0000000b{index:04d}"
    session.add(
        Project(
            id=pid,
            title=f"Truyện {index}",
            slug=f"truyen-{index}",
            source_type=SourceType.SELF_AUTHORED.value,
            rights_status=RightsStatus.PRIVATE_ONLY.value,
            default_language="zh-CN",
            target_language="vi-VN",
        )
    )
    session.flush()
    for ordinal in range(1, chapters + 1):
        chapter_id = f"018f0000-0000-7000-8000-0000000d{index:02d}{ordinal:04d}"
        session.add(
            Chapter(
                id=chapter_id,
                project_id=pid,
                ordinal=ordinal,
                state=ChapterState.IMPORTED.value,
                source_title=f"Chương {ordinal}",
            )
        )
        session.flush()
        if ordinal == 1:
            revision = SourceRevision(
                id=f"018f0000-0000-7000-8000-0000000c{index:04d}",
                chapter_id=chapter_id,
                revision_no=1,
                import_kind=ImportKind.PASTE.value,
                normalized_text=SOURCE_TEXT,
                normalized_sha256=hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest(),
                han_char_count=0,
                total_char_count=len(SOURCE_TEXT),
                normalizer_version="nfc-v1",
            )
            session.add(revision)
            session.flush()
            chapter = session.get(Chapter, chapter_id)
            chapter.active_source_revision_id = revision.id
            session.add(
                SourceSegment(
                    id=f"018f0000-0000-7000-8000-0000000e{index:02d}{ordinal:04d}",
                    source_revision_id=revision.id,
                    segment_index=0,
                    paragraph_start=0,
                    paragraph_end=0,
                    source_text=SOURCE_TEXT,
                    source_sha256=hashlib.sha256(SOURCE_TEXT.encode("utf-8")).hexdigest(),
                    segment_kind="SOURCE",
                )
            )
    session.commit()
    return pid


def _api(engine: Engine) -> TestClient:
    db_path = Path(engine.url.database)
    app = FastAPI()
    app.include_router(create_projects_router(Settings(data_root=db_path.parent), cursor_secret="test-secret"))
    return TestClient(app)


def test_library_page_bounds_mounted_chapters_and_reports_counts(migrated_engine: Engine) -> None:
    session = session_factory(migrated_engine)()
    try:
        _seed_project(session, 1, chapters=120)
        _seed_project(session, 2, chapters=2)
    finally:
        session.close()

    with _api(migrated_engine) as client:
        response = client.get("/api/projects")

        assert response.status_code == 200
        body = response.json()
        assert body["page"]["total"] == 2
        assert body["page"]["count"] == 2
        assert body["page"]["limit"] == DEFAULT_PROJECT_LIMIT
        assert body["page"]["hasMore"] is False
        assert body["page"]["nextCursor"] is None
        assert body["page"]["mountedChaptersPerProject"] == MOUNTED_CHAPTERS_PER_PROJECT

        big = next(project for project in body["projects"] if project["chapterCount"] == 120)
        assert len(big["chapters"]) == MOUNTED_CHAPTERS_PER_PROJECT
        assert [chapter["ordinal"] for chapter in big["chapters"]] == list(
            range(1, MOUNTED_CHAPTERS_PER_PROJECT + 1)
        )
        assert big["firstChapterId"] == big["chapters"][0]["id"]
        # Chapter 31+ is not mounted, and no source text is exposed anywhere.
        assert all("sourceText" not in chapter for chapter in big["chapters"])
        assert SOURCE_TEXT not in response.text
        assert "Chương 31" not in response.text

        small = next(project for project in body["projects"] if project["chapterCount"] == 2)
        assert len(small["chapters"]) == 2


def test_library_page_uses_a_constant_number_of_statements(migrated_engine: Engine) -> None:
    session = session_factory(migrated_engine)()
    try:
        for index in range(1, 6):
            _seed_project(session, index, chapters=5 + index)
    finally:
        session.close()

    statements: list[str] = []

    def before_cursor_execute(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(migrated_engine, "before_cursor_execute", before_cursor_execute)
    try:
        with _api(migrated_engine) as client:
            response = client.get("/api/projects", params={"limit": 5})
    finally:
        event.remove(migrated_engine, "before_cursor_execute", before_cursor_execute)

    assert response.status_code == 200
    assert len(response.json()["projects"]) == 5

    selects = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
    # count + page + grouped counts + windowed first chapters + windowed summaries.
    assert len(selects) <= 6
    # No statement selects every chapter row without a bound.
    assert not any("row_number() OVER" not in statement and "FROM chapters" in statement for statement in selects)


def test_library_page_pages_with_a_signed_cursor_and_rejects_forgery(migrated_engine: Engine) -> None:
    session = session_factory(migrated_engine)()
    try:
        for index in range(1, 6):
            _seed_project(session, index, chapters=1)
    finally:
        session.close()

    queries = ProjectQueries(migrated_engine, cursor_secret="test-secret")
    first = queries.list_projects(limit=2)

    assert len(first.projects) == 2
    assert first.total == 5
    assert first.next_cursor is not None

    second = queries.list_projects(limit=2, cursor=first.next_cursor)
    assert len(second.projects) == 2
    ids = {project.id for project in first.projects} & {project.id for project in second.projects}
    assert ids == set()

    third = queries.list_projects(limit=2, cursor=second.next_cursor)
    assert len(third.projects) == 1
    assert third.next_cursor is None


def test_library_page_rejects_a_forged_cursor_and_bounds_the_limit(migrated_engine: Engine) -> None:
    session = session_factory(migrated_engine)()
    try:
        for index in range(1, 4):
            _seed_project(session, index, chapters=1)
    finally:
        session.close()

    queries = ProjectQueries(migrated_engine, cursor_secret="test-secret")

    with pytest.raises(InvalidCursor):
        queries.list_projects(cursor="not-a-real-cursor")

    # A forged-but-well-formed payload must fail the signature check.
    import base64
    import json

    forged = base64.urlsafe_b64encode(
        json.dumps({"created_at": "2026-01-01T00:00:00+00:00", "id": "x", "sig": "0" * 64}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    with pytest.raises(InvalidCursor):
        queries.list_projects(cursor=forged)

    # limit is bounded to 100 rows (and a silly value falls back to the max page).
    with _api(migrated_engine) as client:
        huge = client.get("/api/projects", params={"limit": 10_000})
        assert huge.status_code == 200
        assert huge.json()["page"]["limit"] == MAX_PROJECT_LIMIT
        assert len(huge.json()["projects"]) <= MAX_PROJECT_LIMIT

        invalid = client.get("/api/projects", params={"cursor": "forged"})
        assert invalid.status_code == 400
        assert invalid.json()["detail"] == "LIBRARY_CURSOR_INVALID"


def test_library_page_filters_by_title_server_side(migrated_engine: Engine) -> None:
    session = session_factory(migrated_engine)()
    try:
        _seed_project(session, 1, chapters=1)
        _seed_project(session, 2, chapters=1)
        _seed_project(session, 3, chapters=1)
    finally:
        session.close()

    with _api(migrated_engine) as client:
        filtered = client.get("/api/projects", params={"q": "Truyện 2"})

        assert filtered.status_code == 200
        body = filtered.json()
        assert body["page"]["total"] == 1
        assert [project["title"] for project in body["projects"]] == ["Truyện 2"]
