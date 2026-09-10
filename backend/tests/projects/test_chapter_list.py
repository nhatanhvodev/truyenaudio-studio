"""V02 task 64 - the chapter page must filter and paginate on the SERVER.

Measured failure (fixture CHLIST, task 62): GET
/api/projects/{id}/chapters?limit=100&q=ZZQ-MARKER returned 100 rows with
total=1000 - exactly the unfiltered total - because the route only accepted
limit/cursor and ChapterQueries.list_chapters had no filter branch. A client
therefore had to filter inside the 100 rows it had already mounted.

These tests fix the contract in place:

- q (and its alias filter) searches source_title/translated_title, status keeps
  one ChapterState; both run in SQL **before** paging, so total counts the
  filtered set and the cursor walks that same set;
- a request without filters keeps the previous response, cursor format included
  (a cursor minted before filtering existed still verifies);
- an empty result set is a normal empty page, never an error;
- one page costs a constant number of statements (count, page, then one
  grouped/IN query per summary facet) instead of the three-per-chapter N+1
  (~302 statements for 100 rows) that _chapter_summary used to run.
"""

from __future__ import annotations

import base64
from hashlib import sha256
import hmac
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event

from app.api.projects import create_projects_router
from app.contracts import (
    ChapterState,
    ImportKind,
    JobKind,
    JobStatus,
    QaCategory,
    QaSeverity,
    QaStatus,
    RightsStatus,
    RunStatus,
    SourceType,
)
from app.db.base import session_factory
from app.db.models import Chapter, Job, Project, QaIssue, SourceRevision, TranslationRun
from app.settings.config import Settings

CURSOR_SECRET = "task-64-cursor-secret"
MARKER = "ZZQ-MARKER"
PROJECT_ID = "018f0000-0000-7000-8000-a00000000640"
CONTRACT_PROJECT_ID = "018f0000-0000-7000-8000-a00000000641"
DEFAULT_CHAPTERS = 1_000
MARKER_CHAPTERS = 12
NORMALIZED_EVERY = 5
MAX_STATEMENTS_PER_PAGE = 12


def _chapter_id(ordinal: int) -> str:
    return f"018f0000-0000-7000-8000-b{ordinal:011d}"


def _seed_project(
    session,
    *,
    chapters: int = DEFAULT_CHAPTERS,
    marked: int = MARKER_CHAPTERS,
    project_id: str = PROJECT_ID,
) -> str:
    """A project of real chapter rows: the first `marked` carry the CHLIST marker.

    Every 5th chapter starts NORMALIZED (the rest IMPORTED) and the last two
    chapters carry a Vietnamese translated title only, so the search can be
    exercised against both title columns and against a NULL source_title.
    """

    session.add(
        Project(
            id=project_id,
            title="Truyện dài",
            slug=f"truyen-dai-{chapters}",
            source_type=SourceType.SELF_AUTHORED.value,
            rights_status=RightsStatus.PRIVATE_ONLY.value,
        )
    )
    session.flush()
    for ordinal in range(1, chapters + 1):
        if ordinal <= marked:
            source_title: str | None = f"{MARKER} chương đánh dấu {ordinal}"
        elif ordinal == chapters:
            source_title = None
        else:
            source_title = f"Chương {ordinal}"
        if ordinal == chapters:
            translated_title: str | None = "Kiếm Lai - bản dịch"
        elif ordinal == chapters - 1:
            translated_title = "Chương cuối Kiếm Lai"
        else:
            translated_title = None
        session.add(
            Chapter(
                id=_chapter_id(ordinal),
                project_id=project_id,
                ordinal=ordinal,
                source_title=source_title,
                translated_title=translated_title,
                state=(
                    ChapterState.NORMALIZED.value
                    if ordinal % NORMALIZED_EVERY == 1
                    else ChapterState.IMPORTED.value
                ),
            )
        )
    session.flush()
    session.commit()
    return project_id


def _seed_summary_facets(session, project_id: str, ordinals: list[int]) -> None:
    """Real progress/cost/issue rows, so the summary values can be asserted."""

    for ordinal in ordinals:
        chapter = session.get(Chapter, _chapter_id(ordinal))
        revision_id = f"018f0000-0000-7000-8000-c{ordinal:011d}"
        session.add(
            SourceRevision(
                id=revision_id,
                chapter_id=chapter.id,
                revision_no=1,
                import_kind=ImportKind.PASTE.value,
                normalized_text=f"source {ordinal}",
                normalized_sha256=sha256(f"source {ordinal}".encode()).hexdigest(),
                han_char_count=0,
                total_char_count=8,
                normalizer_version="nfc-v1",
            )
        )
        session.flush()
        chapter.active_source_revision_id = revision_id
        if ordinal % 2 == 0:
            run_id = f"018f0000-0000-7000-8000-d{ordinal:011d}"
            session.add(
                TranslationRun(
                    id=run_id,
                    chapter_id=chapter.id,
                    source_revision_id=revision_id,
                    prompt_version="v1",
                    status=RunStatus.APPROVED.value,
                    translation_text_sha256=sha256(f"translation {ordinal}".encode()).hexdigest(),
                    estimated_cost_vnd=1_000 + ordinal,
                    actual_cost_vnd=500 + ordinal,
                )
            )
            session.flush()
            chapter.approved_translation_run_id = run_id
        statuses = (
            [JobStatus.SUCCEEDED.value, JobStatus.QUEUED.value]
            if ordinal % 2 == 0
            else [JobStatus.BILLING_UNKNOWN.value]
        )
        for index, status in enumerate(statuses):
            session.add(
                Job(
                    id=f"018f0000-0000-7000-8000-e{ordinal:09d}{index:02d}",
                    kind=JobKind.TRANSLATE.value,
                    status=status,
                    project_id=project_id,
                    chapter_id=chapter.id,
                    idempotency_key=f"translate-{ordinal}-{index}",
                )
            )
        for index, qa_status in enumerate([QaStatus.OPEN.value, QaStatus.FIXED.value]):
            session.add(
                QaIssue(
                    id=f"018f0000-0000-7000-8000-f{ordinal:09d}{index:02d}",
                    chapter_id=chapter.id,
                    category=QaCategory.ACCURACY.value,
                    severity=QaSeverity.MINOR.value,
                    status=qa_status,
                )
            )
    session.flush()
    session.commit()


def _seed(engine: Engine, **kwargs) -> str:
    session = session_factory(engine)()
    try:
        return _seed_project(session, **kwargs)
    finally:
        session.close()


def _api(engine: Engine) -> TestClient:
    db_path = Path(engine.url.database)
    app = FastAPI()
    app.include_router(
        create_projects_router(Settings(data_root=db_path.parent), cursor_secret=CURSOR_SECRET)
    )
    return TestClient(app)


def _url(project_id: str = PROJECT_ID) -> str:
    return f"/api/projects/{project_id}/chapters"


def _legacy_cursor(ordinal: int, chapter_id: str) -> str:
    """The exact HMAC envelope the route minted BEFORE filtering existed.

    The payload is unchanged on purpose: it is a position in the (ordinal, id)
    order, which does not depend on the filter.
    """

    payload = {"ordinal": ordinal, "id": chapter_id}
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(CURSOR_SECRET.encode("utf-8"), payload_bytes, sha256).hexdigest()
    envelope = {**payload, "sig": signature}
    return (
        base64.urlsafe_b64encode(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )


def test_query_filter_runs_in_sql_before_paging(migrated_engine: Engine) -> None:
    """The measured CHLIST probe: q=marker must not return a full unfiltered page."""

    _seed(migrated_engine)
    with _api(migrated_engine) as client:
        response = client.get(_url(), params={"limit": 100, "q": MARKER})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == MARKER_CHAPTERS  # 12, not the unfiltered 1000
    assert len(body["items"]) == MARKER_CHAPTERS  # 12 rows, not a mounted page of 100
    assert body["nextCursor"] is None
    assert [item["ordinal"] for item in body["items"]] == list(range(1, MARKER_CHAPTERS + 1))
    assert all(MARKER in item["sourceTitle"] for item in body["items"])


def test_query_filter_searches_both_title_columns(migrated_engine: Engine) -> None:
    _seed(migrated_engine, chapters=300, marked=0)
    with _api(migrated_engine) as client:
        response = client.get(_url(), params={"limit": 100, "q": "Kiếm Lai"})

    body = response.json()
    assert response.status_code == 200
    # Ordinal 300 has no source_title at all - the translated title still matches.
    assert body["total"] == 2
    assert [item["ordinal"] for item in body["items"]] == [299, 300]
    assert all("Kiếm Lai" in item["translatedTitle"] for item in body["items"])


def test_search_is_unicode_and_ascii_case_tolerant(migrated_engine: Engine) -> None:
    _seed(migrated_engine, chapters=300, marked=0)
    with _api(migrated_engine) as client:
        accented = client.get(_url(), params={"limit": 50, "q": "chương 299"})
        upper = client.get(_url(), params={"limit": 50, "q": "KIẾM LAI"})

    assert accented.status_code == 200
    assert accented.json()["total"] == 1
    assert [item["ordinal"] for item in accented.json()["items"]] == [299]
    # SQLite folds ASCII only, exactly like the library search: the uppercase
    # needle still finds the accented lowercase title because the accents match.
    assert upper.json()["total"] == 2


def test_filter_alias_matches_the_q_parameter(migrated_engine: Engine) -> None:
    _seed(migrated_engine)
    with _api(migrated_engine) as client:
        alias = client.get(_url(), params={"limit": 100, "filter": MARKER})

    assert alias.status_code == 200
    assert alias.json()["total"] == MARKER_CHAPTERS
    assert len(alias.json()["items"]) == MARKER_CHAPTERS


def test_status_filter_keeps_only_that_state(migrated_engine: Engine) -> None:
    _seed(migrated_engine)
    expected = len(range(1, DEFAULT_CHAPTERS + 1, NORMALIZED_EVERY))
    with _api(migrated_engine) as client:
        response = client.get(
            _url(), params={"limit": 100, "status": ChapterState.NORMALIZED.value}
        )

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == expected == 200
    assert len(body["items"]) == 100
    assert body["nextCursor"] is not None
    assert {item["state"] for item in body["items"]} == {ChapterState.NORMALIZED.value}
    assert [item["ordinal"] for item in body["items"]] == list(range(1, 500, NORMALIZED_EVERY))


def test_query_and_status_filters_combine(migrated_engine: Engine) -> None:
    """Both filters narrow the same set: the marked chapters that are NORMALIZED."""

    _seed(migrated_engine)
    marked_normalized = [
        ordinal for ordinal in range(1, MARKER_CHAPTERS + 1) if ordinal % NORMALIZED_EVERY == 1
    ]
    with _api(migrated_engine) as client:
        both = client.get(
            _url(), params={"limit": 100, "q": MARKER, "status": ChapterState.NORMALIZED.value}
        )
        others = client.get(
            _url(), params={"limit": 100, "q": MARKER, "status": ChapterState.IMPORTED.value}
        )

    assert both.json()["total"] == len(marked_normalized) == 3
    assert [item["ordinal"] for item in both.json()["items"]] == marked_normalized
    assert others.json()["total"] == MARKER_CHAPTERS - len(marked_normalized) == 9


def test_filtered_paging_walks_the_filtered_set(migrated_engine: Engine) -> None:
    """q + cursor: every filtered row exactly once, no gap, no duplicate."""

    _seed(migrated_engine, chapters=300, marked=250)
    seen: list[int] = []
    pages = 0
    cursor: str | None = None
    with _api(migrated_engine) as client:
        while True:
            params: dict[str, object] = {"limit": 25, "q": MARKER}
            if cursor is not None:
                params["cursor"] = cursor
            response = client.get(_url(), params=params)
            assert response.status_code == 200
            body = response.json()
            assert body["total"] == 250
            seen.extend(item["ordinal"] for item in body["items"])
            pages += 1
            cursor = body["nextCursor"]
            assert pages <= 20
            if cursor is None:
                break

    assert pages == 10
    assert seen == list(range(1, 251))
    assert len(seen) == len(set(seen))


def test_filter_with_no_match_is_an_empty_page(migrated_engine: Engine) -> None:
    _seed(migrated_engine)
    with _api(migrated_engine) as client:
        response = client.get(_url(), params={"limit": 100, "q": "không-có-gì-khớp"})

    assert response.status_code == 200
    assert response.json() == {"items": [], "nextCursor": None, "total": 0}


def test_no_filter_keeps_the_previous_response(migrated_engine: Engine) -> None:
    """Backwards compatibility: same input, same output as before filtering."""

    _seed(migrated_engine)
    with _api(migrated_engine) as client:
        response = client.get(_url(), params={"limit": 25})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == DEFAULT_CHAPTERS
    assert [item["ordinal"] for item in body["items"]] == list(range(1, 26))
    assert body["nextCursor"] is not None

    # A blank search is not a filter at all (same as the library q).
    with _api(migrated_engine) as client:
        blank = client.get(_url(), params={"limit": 25, "q": "   "})
    assert blank.json()["total"] == DEFAULT_CHAPTERS
    assert [item["ordinal"] for item in blank.json()["items"]] == list(range(1, 26))


def test_a_cursor_minted_before_filtering_still_verifies(migrated_engine: Engine) -> None:
    """Pre-change cursors keep working (payload/signature unchanged, never a 500)."""

    _seed(migrated_engine, chapters=300, marked=250)
    legacy = _legacy_cursor(100, _chapter_id(100))
    with _api(migrated_engine) as client:
        plain = client.get(_url(), params={"limit": 25, "cursor": legacy})
        filtered = client.get(_url(), params={"limit": 25, "q": MARKER, "cursor": legacy})

    assert plain.status_code == 200
    assert [item["ordinal"] for item in plain.json()["items"]] == list(range(101, 126))
    assert plain.json()["total"] == 300
    assert filtered.status_code == 200
    assert [item["ordinal"] for item in filtered.json()["items"]] == list(range(101, 126))
    assert filtered.json()["total"] == 250


def test_a_forged_cursor_is_a_400_not_a_500(migrated_engine: Engine) -> None:
    _seed(migrated_engine, chapters=300, marked=250)
    with _api(migrated_engine) as client:
        tampered = client.get(_url(), params={"limit": 25, "q": MARKER, "cursor": "tampered"})

    assert tampered.status_code == 400
    assert tampered.json()["detail"] == "CHAPTER_CURSOR_INVALID"


def test_an_unknown_status_is_a_400_not_a_500(migrated_engine: Engine) -> None:
    _seed(migrated_engine, chapters=20, marked=0)
    with _api(migrated_engine) as client:
        response = client.get(_url(), params={"limit": 25, "status": "NOT_A_CHAPTER_STATE"})

    assert response.status_code == 400
    assert response.json()["detail"] == "CHAPTER_STATUS_INVALID"


def test_chapter_page_keeps_the_summary_contract(migrated_engine: Engine) -> None:
    """The batched summaries return exactly the fields/values of the N+1 version."""

    session = session_factory(migrated_engine)()
    try:
        _seed_project(
            session, chapters=4, marked=0, project_id=CONTRACT_PROJECT_ID
        )
        _seed_summary_facets(session, CONTRACT_PROJECT_ID, [1, 2, 3, 4])
    finally:
        session.close()

    with _api(migrated_engine) as client:
        response = client.get(_url(CONTRACT_PROJECT_ID), params={"limit": 25})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert [item["ordinal"] for item in body["items"]] == [1, 2, 3, 4]

    for item in body["items"]:
        assert set(item) == {
            "id",
            "projectId",
            "ordinal",
            "sourceTitle",
            "translatedTitle",
            "state",
            "progress",
            "cost",
            "issues",
            "hashes",
        }
        assert set(item["progress"]) == {"current", "total"}
        assert set(item["cost"]) == {"estimatedVnd", "actualVnd"}
        assert set(item["hashes"]) == {"sourceSha256", "translationSha256"}
        assert item["hashes"]["sourceSha256"] == sha256(
            f"source {item['ordinal']}".encode()
        ).hexdigest()

    first, second = body["items"][0], body["items"][1]
    # Odd ordinal: one BILLING_UNKNOWN job (1/1), one OPEN + one FIXED issue,
    # no approved run.
    assert (first["progress"], first["issues"]) == ({"current": 1, "total": 1}, 1)
    assert (first["cost"], first["hashes"]["translationSha256"]) == (
        {"estimatedVnd": None, "actualVnd": None},
        None,
    )
    # Even ordinal: SUCCEEDED + QUEUED (1/2), an approved run and its QA counts.
    assert (second["progress"], second["issues"]) == ({"current": 1, "total": 2}, 1)
    assert second["cost"] == {"estimatedVnd": 1_002, "actualVnd": 502}
    assert second["hashes"]["translationSha256"] == sha256(b"translation 2").hexdigest()


def test_a_chapter_with_no_jobs_still_reports_zero_progress(migrated_engine: Engine) -> None:
    _seed(migrated_engine, chapters=5, marked=0)
    with _api(migrated_engine) as client:
        body = client.get(_url(), params={"limit": 25}).json()

    assert body["total"] == 5
    assert all(item["progress"] == {"current": 0, "total": 0} for item in body["items"])
    assert all(item["issues"] == 0 for item in body["items"])
    assert all(item["cost"] == {"estimatedVnd": None, "actualVnd": None} for item in body["items"])
    assert all(item["hashes"]["sourceSha256"] is None for item in body["items"])


def test_chapter_page_uses_a_constant_number_of_statements(migrated_engine: Engine) -> None:
    """No N+1: the page cost must not follow the row count, with or without q."""

    session = session_factory(migrated_engine)()
    try:
        _seed_project(session, chapters=DEFAULT_CHAPTERS, marked=MARKER_CHAPTERS)
        _seed_summary_facets(session, PROJECT_ID, list(range(1, 101)))
    finally:
        session.close()

    counts: list[int] = []
    statements: list[str] = []

    def before_cursor_execute(_conn, _cursor, statement, _params, _context, _many) -> None:
        statements.append(statement)

    # Attached at the Engine class level (as the benchmark harness does): the
    # route builds a fresh engine per request.
    event.listen(Engine, "before_cursor_execute", before_cursor_execute)
    try:
        with _api(migrated_engine) as client:
            bodies = []
            for params in (
                {"limit": 10},
                {"limit": 100},
                {"limit": 500},
                {"limit": 100, "q": MARKER},
                {"limit": 100, "status": ChapterState.NORMALIZED.value},
                {"limit": 100, "q": "không-có-gì-khớp"},
            ):
                statements.clear()
                response = client.get(_url(), params=params)
                assert response.status_code == 200
                bodies.append(response.json())
                counts.append(len(statements))
    finally:
        event.remove(Engine, "before_cursor_execute", before_cursor_execute)

    assert all(count <= MAX_STATEMENTS_PER_PAGE for count in counts), counts
    # limit=10 and limit=100 (and the clamped limit=500) cost the SAME number of
    # statements: nothing in the page grows with the number of mounted chapters.
    assert counts[0] == counts[1] == counts[2], counts
    # A filtered page costs the same as an unfiltered one, and an empty result
    # is cheaper (no rows to summarise) - never more.
    assert counts[3] == counts[4] == counts[0], counts
    assert counts[5] <= counts[0], counts
    assert bodies[0]["total"] == DEFAULT_CHAPTERS
    assert bodies[3]["total"] == MARKER_CHAPTERS
    assert bodies[5]["total"] == 0
