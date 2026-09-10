from __future__ import annotations

from dataclasses import dataclass
import base64
from collections.abc import Sequence
from hashlib import sha256
import hmac
import json
from typing import Generic, TypeVar

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import sessionmaker

from app.contracts import ChapterState
from app.db.base import session_factory
from app.db.models import Chapter, Job, Project, QaIssue, SourceRevision, TranslationRun


DEFAULT_CHAPTER_LIMIT = 25
MAX_CHAPTER_LIMIT = 100
DEFAULT_PROJECT_LIMIT = 20
MAX_PROJECT_LIMIT = 100
"""U03: a library page never mounts more than 100 rows, and each project row
carries at most `MOUNTED_CHAPTERS_PER_PROJECT` chapter summaries — the rest is a
count, so a 10k-chapter project cannot pull its chapters (or any source text)
into the library payload."""
MOUNTED_CHAPTERS_PER_PROJECT = 30
T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None
    total: int


@dataclass(frozen=True)
class ChapterProgress:
    current: int
    total: int


@dataclass(frozen=True)
class ChapterCost:
    estimated_vnd: int | None
    actual_vnd: int | None


@dataclass(frozen=True)
class ChapterHashes:
    source_sha256: str | None
    translation_sha256: str | None


@dataclass(frozen=True)
class ChapterSummary:
    id: str
    project_id: str
    ordinal: int
    source_title: str | None
    translated_title: str | None
    state: ChapterState
    progress: ChapterProgress
    cost: ChapterCost
    issues: int
    hashes: ChapterHashes


class InvalidCursor(ValueError):
    pass


@dataclass(frozen=True)
class LibraryChapter:
    id: str
    ordinal: int
    title: str | None
    state: str


@dataclass(frozen=True)
class LibraryProject:
    id: str
    title: str
    slug: str
    source_type: str
    rights_status: str
    created_at: str | None
    updated_at: str | None
    chapter_count: int
    first_chapter_id: str | None
    chapters: tuple[LibraryChapter, ...]


@dataclass(frozen=True)
class LibraryPage:
    projects: tuple[LibraryProject, ...]
    next_cursor: str | None
    total: int
    limit: int


class ProjectQueries:
    """Cursor-paginated library listing (U03).

    Ordering is `created_at DESC, id DESC`; the cursor is signed so a client
    cannot forge a position. The page is built with a **constant number of
    statements** (page rows, grouped chapter counts, windowed chapter summaries)
    instead of one query per project, and chapter summaries are limited by a
    window function — a project with 10k chapters still returns a fixed page.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        cursor_secret: str,
        session_factory_: sessionmaker | None = None,
    ) -> None:
        self.engine = engine
        self.cursor_secret = cursor_secret.encode("utf-8")
        self._session_factory = session_factory_ or session_factory(engine)

    def list_projects(
        self,
        *,
        limit: int = DEFAULT_PROJECT_LIMIT,
        cursor: str | None = None,
        query: str | None = None,
        archived: bool = False,
    ) -> LibraryPage:
        bounded_limit = _bounded_project_limit(limit)
        after = self._decode_cursor(cursor) if cursor else None
        needle = (query or "").strip().casefold()

        with self._session_factory() as session:
            filters = [Project.archived_at.is_(None) if not archived else Project.archived_at.is_not(None)]
            if needle:
                filters.append(
                    func.lower(Project.title).like(f"%{needle}%") | func.lower(Project.slug).like(f"%{needle}%")
                )
            total = int(
                session.scalar(select(func.count()).select_from(Project).where(*filters)) or 0
            )

            statement = select(Project).where(*filters)
            if after is not None:
                created_at, project_id = after
                statement = statement.where(
                    (Project.created_at < created_at)
                    | ((Project.created_at == created_at) & (Project.id < project_id))
                )
            rows = session.scalars(
                statement.order_by(Project.created_at.desc(), Project.id.desc()).limit(bounded_limit + 1)
            ).all()
            visible = list(rows[:bounded_limit])
            project_ids = [project.id for project in visible]

            counts = _chapter_counts(session, project_ids)
            mounted = _mounted_chapters(session, project_ids)
            first_chapters = _first_chapter_ids(session, project_ids)

            projects = tuple(
                LibraryProject(
                    id=project.id,
                    title=project.title,
                    slug=project.slug,
                    source_type=project.source_type,
                    rights_status=project.rights_status,
                    created_at=project.created_at.isoformat() if project.created_at else None,
                    updated_at=project.updated_at.isoformat() if project.updated_at else None,
                    chapter_count=counts.get(project.id, 0),
                    first_chapter_id=first_chapters.get(project.id),
                    chapters=mounted.get(project.id, ()),
                )
                for project in visible
            )

            next_cursor = None
            if len(rows) > bounded_limit and visible:
                last = visible[-1]
                next_cursor = self._encode_cursor(last.created_at, last.id)

        return LibraryPage(
            projects=projects,
            next_cursor=next_cursor,
            total=total,
            limit=bounded_limit,
        )

    def _encode_cursor(self, created_at, project_id: str) -> str:
        payload = {"created_at": created_at.isoformat(), "id": project_id}
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self.cursor_secret, payload_bytes, sha256).hexdigest()
        envelope = {**payload, "sig": signature}
        token = base64.urlsafe_b64encode(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        return token.rstrip("=")

    def _decode_cursor(self, cursor: str):
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            envelope = json.loads(raw)
            created_at_raw = envelope["created_at"]
            project_id = envelope["id"]
            signature = envelope["sig"]
            if type(created_at_raw) is not str or type(project_id) is not str or type(signature) is not str:
                raise ValueError
            payload = {"created_at": created_at_raw, "id": project_id}
            payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            expected = hmac.new(self.cursor_secret, payload_bytes, sha256).hexdigest()
        except Exception as exc:
            raise InvalidCursor("LIBRARY_CURSOR_INVALID") from exc
        if not hmac.compare_digest(signature, expected):
            raise InvalidCursor("LIBRARY_CURSOR_INVALID")
        return _parse_timestamp(created_at_raw), project_id


def _parse_timestamp(value: str):
    from datetime import datetime

    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - defensive
        raise InvalidCursor("LIBRARY_CURSOR_INVALID") from exc


def _chapter_counts(session, project_ids: Sequence[str]) -> dict[str, int]:
    if not project_ids:
        return {}
    rows = session.execute(
        select(Chapter.project_id, func.count())
        .where(Chapter.project_id.in_(project_ids))
        .group_by(Chapter.project_id)
    ).all()
    return {row[0]: int(row[1]) for row in rows}


def _first_chapter_ids(session, project_ids: Sequence[str]) -> dict[str, str]:
    if not project_ids:
        return {}
    ranked = (
        select(
            Chapter.project_id.label("project_id"),
            Chapter.id.label("chapter_id"),
            func.row_number()
            .over(partition_by=Chapter.project_id, order_by=(Chapter.ordinal.asc(), Chapter.id.asc()))
            .label("rank"),
        )
        .where(Chapter.project_id.in_(project_ids))
        .subquery()
    )
    rows = session.execute(
        select(ranked.c.project_id, ranked.c.chapter_id).where(ranked.c.rank == 1)
    ).all()
    return {row[0]: row[1] for row in rows}


def _mounted_chapters(session, project_ids: Sequence[str]) -> dict[str, tuple[LibraryChapter, ...]]:
    """First `MOUNTED_CHAPTERS_PER_PROJECT` chapter summaries per project (windowed)."""
    if not project_ids:
        return {}
    ranked = (
        select(
            Chapter.project_id.label("project_id"),
            Chapter.id.label("chapter_id"),
            Chapter.ordinal.label("ordinal"),
            Chapter.source_title.label("title"),
            Chapter.state.label("state"),
            func.row_number()
            .over(partition_by=Chapter.project_id, order_by=(Chapter.ordinal.asc(), Chapter.id.asc()))
            .label("rank"),
        )
        .where(Chapter.project_id.in_(project_ids))
        .subquery()
    )
    rows = session.execute(
        select(
            ranked.c.project_id,
            ranked.c.chapter_id,
            ranked.c.ordinal,
            ranked.c.title,
            ranked.c.state,
        )
        .where(ranked.c.rank <= MOUNTED_CHAPTERS_PER_PROJECT)
        .order_by(ranked.c.project_id, ranked.c.ordinal, ranked.c.chapter_id)
    ).all()
    grouped: dict[str, list[LibraryChapter]] = {}
    for project_id, chapter_id, ordinal, title, state in rows:
        grouped.setdefault(project_id, []).append(
            LibraryChapter(id=chapter_id, ordinal=int(ordinal), title=title, state=state)
        )
    return {project_id: tuple(items) for project_id, items in grouped.items()}


def _bounded_project_limit(limit: int) -> int:
    if limit < 1:
        return MAX_PROJECT_LIMIT
    return min(limit, MAX_PROJECT_LIMIT)


class ChapterQueries:
    def __init__(
        self,
        engine: Engine,
        *,
        cursor_secret: str,
        session_factory_: sessionmaker | None = None,
    ) -> None:
        self.engine = engine
        self.cursor_secret = cursor_secret.encode("utf-8")
        self._session_factory = session_factory_ or session_factory(engine)

    def list_chapters(
        self,
        project_id: str,
        *,
        limit: int = DEFAULT_CHAPTER_LIMIT,
        cursor: str | None = None,
        query: str | None = None,
        state: ChapterState | str | None = None,
    ) -> Page[ChapterSummary]:
        """One page of chapter summaries, filtered **in SQL** (V02 task 64).

        `query` (HTTP `q`, also accepted as `filter`) searches
        `source_title`/`translated_title` the same way the library search does;
        `state` (HTTP `status`) keeps one :class:`ChapterState`. Both filters are
        applied to the counting statement *and* to the page statement, i.e.
        **before paging**: `total` counts the filtered set and the cursor walks that
        same filtered set, so a client never has to filter inside the rows it
        already mounted.

        With no filter the SQL and the response are exactly the previous
        behaviour. The cursor payload is unchanged (`{ordinal, id}`): it encodes a
        position in the `(ordinal, id)` order, which does not depend on the
        filter, so cursors minted before filtering existed keep working — a
        filter-less (or foreign) cursor is never an error and can never turn
        into a 500. Callers replay the same filter on every page, as in any
        other keyset pagination. An unknown `state` raises `ValueError`.

        The page costs a **constant** number of statements (count, page, then one
        grouped/IN query per summary facet) instead of one query per chapter.
        """
        bounded_limit = _bounded_limit(limit)
        after = self._decode_cursor(cursor) if cursor else None
        filters = _chapter_filters(project_id, query=query, state=state)
        with self._session_factory() as session:
            total = int(
                session.scalar(select(func.count()).select_from(Chapter).where(*filters)) or 0
            )
            statement = select(Chapter).where(*filters)
            if after is not None:
                ordinal, chapter_id = after
                statement = statement.where(
                    (Chapter.ordinal > ordinal) | ((Chapter.ordinal == ordinal) & (Chapter.id > chapter_id))
                )
            chapters = session.scalars(
                statement.order_by(Chapter.ordinal.asc(), Chapter.id.asc()).limit(bounded_limit + 1)
            ).all()
            visible = tuple(chapters[:bounded_limit])
            summaries = _chapter_summaries(session, visible)
            next_cursor = None
            if len(chapters) > bounded_limit and visible:
                last = visible[-1]
                next_cursor = self._encode_cursor(last.ordinal, last.id)
        return Page(items=summaries, next_cursor=next_cursor, total=total)

    def _encode_cursor(self, ordinal: int, chapter_id: str) -> str:
        payload = {"ordinal": ordinal, "id": chapter_id}
        payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self.cursor_secret, payload_bytes, sha256).hexdigest()
        envelope = {**payload, "sig": signature}
        token = base64.urlsafe_b64encode(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        return token.rstrip("=")

    def _decode_cursor(self, cursor: str) -> tuple[int, str]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            envelope = json.loads(raw)
            ordinal = envelope["ordinal"]
            chapter_id = envelope["id"]
            signature = envelope["sig"]
            if type(ordinal) is not int or type(chapter_id) is not str or type(signature) is not str:
                raise ValueError
            payload = {"ordinal": ordinal, "id": chapter_id}
            payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            expected = hmac.new(self.cursor_secret, payload_bytes, sha256).hexdigest()
        except Exception as exc:
            raise InvalidCursor("CHAPTER_CURSOR_INVALID") from exc
        if not hmac.compare_digest(signature, expected):
            raise InvalidCursor("CHAPTER_CURSOR_INVALID")
        return ordinal, chapter_id


def _bounded_limit(limit: int) -> int:
    if limit < 1:
        return DEFAULT_CHAPTER_LIMIT
    return min(limit, MAX_CHAPTER_LIMIT)


# Job statuses that count as finished for a chapter's progress bar (unchanged set).
_TERMINAL_JOB_STATUSES = frozenset(
    {"SUCCEEDED", "FAILED", "CANCELED", "BLOCKED_BUDGET", "BILLING_UNKNOWN"}
)


def _chapter_filters(
    project_id: str,
    *,
    query: str | None,
    state: ChapterState | str | None,
) -> list:
    """SQL filters for one chapter page — the same search the library uses.

    The needle is stripped/casefolded in Python and matched with
    `lower(column) LIKE %needle%` over `source_title` and
    `translated_title`, exactly like `ProjectQueries.list_projects` matches a
    project title/slug (SQLite folds ASCII, so a Vietnamese needle keeps its
    diacritics on both sides). A blank needle is not a filter at all, and neither
    is a `None` state, so a request without filters runs the same SQL as before.

    An unknown `state` raises :class:`ValueError` (the route turns that into a
    400), never a silent no-op.
    """

    filters = [Chapter.project_id == project_id]
    needle = (query or "").strip().casefold()
    if needle:
        filters.append(
            func.lower(Chapter.source_title).like(f"%{needle}%")
            | func.lower(Chapter.translated_title).like(f"%{needle}%")
        )
    if state is not None:
        filters.append(Chapter.state == ChapterState(state).value)
    return filters


def _chapter_summaries(session, chapters: Sequence[Chapter]) -> tuple[ChapterSummary, ...]:
    """Summaries for a whole page in a **constant** number of statements.

    Four set-based queries cover the page (job progress, source hash, approved
    run, open QA count) instead of the three-per-chapter N+1 that made a 100-row
    page cost ~302 statements. Every field keeps the value it had before.
    """

    if not chapters:
        return ()
    chapter_ids = [chapter.id for chapter in chapters]
    progress = _progress_by_chapter(session, chapter_ids)
    source_hashes = _source_hashes(
        session,
        [chapter.active_source_revision_id for chapter in chapters if chapter.active_source_revision_id],
    )
    runs = _approved_runs(
        session,
        [chapter.approved_translation_run_id for chapter in chapters if chapter.approved_translation_run_id],
    )
    issues = _open_qa_counts(session, chapter_ids)

    summaries = []
    for chapter in chapters:
        translation = runs.get(chapter.approved_translation_run_id or "")
        summaries.append(
            ChapterSummary(
                id=chapter.id,
                project_id=chapter.project_id,
                ordinal=chapter.ordinal,
                source_title=chapter.source_title,
                translated_title=chapter.translated_title,
                state=ChapterState(chapter.state),
                progress=progress.get(chapter.id, ChapterProgress(current=0, total=0)),
                cost=ChapterCost(
                    estimated_vnd=translation.estimated_cost_vnd if translation else None,
                    actual_vnd=translation.actual_cost_vnd if translation else None,
                ),
                issues=issues.get(chapter.id, 0),
                hashes=ChapterHashes(
                    source_sha256=source_hashes.get(chapter.active_source_revision_id or ""),
                    translation_sha256=translation.translation_text_sha256 if translation else None,
                ),
            )
        )
    return tuple(summaries)


def _progress_by_chapter(session, chapter_ids: Sequence[str]) -> dict[str, ChapterProgress]:
    """`(current, total)` per chapter from one grouped job query (no N+1)."""

    rows = session.execute(
        select(Job.chapter_id, Job.status, func.count())
        .where(Job.chapter_id.in_(chapter_ids))
        .group_by(Job.chapter_id, Job.status)
    ).all()
    buckets: dict[str, list[int]] = {}
    for chapter_id, status, count in rows:
        bucket = buckets.setdefault(chapter_id, [0, 0])
        bucket[1] += int(count)
        if status in _TERMINAL_JOB_STATUSES:
            bucket[0] += int(count)
    return {
        chapter_id: ChapterProgress(current=current, total=total)
        for chapter_id, (current, total) in buckets.items()
    }


def _source_hashes(session, revision_ids: Sequence[str]) -> dict[str, str | None]:
    if not revision_ids:
        return {}
    rows = session.execute(
        select(SourceRevision.id, SourceRevision.normalized_sha256).where(
            SourceRevision.id.in_(revision_ids)
        )
    ).all()
    return {row[0]: row[1] for row in rows}


def _approved_runs(session, run_ids: Sequence[str]) -> dict[str, TranslationRun]:
    if not run_ids:
        return {}
    runs: Sequence[TranslationRun] = session.scalars(
        select(TranslationRun).where(TranslationRun.id.in_(run_ids))
    ).all()
    return {run.id: run for run in runs}


def _open_qa_counts(session, chapter_ids: Sequence[str]) -> dict[str, int]:
    rows = session.execute(
        select(QaIssue.chapter_id, func.count())
        .where(QaIssue.chapter_id.in_(chapter_ids), QaIssue.status == "OPEN")
        .group_by(QaIssue.chapter_id)
    ).all()
    return {row[0]: int(row[1]) for row in rows}
