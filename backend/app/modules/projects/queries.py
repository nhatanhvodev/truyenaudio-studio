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
from app.db.models import Chapter, Job, QaIssue, SourceRevision, TranslationRun


DEFAULT_CHAPTER_LIMIT = 25
MAX_CHAPTER_LIMIT = 100
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
    ) -> Page[ChapterSummary]:
        bounded_limit = _bounded_limit(limit)
        after = self._decode_cursor(cursor) if cursor else None
        with self._session_factory() as session:
            total = int(session.scalar(select(func.count()).select_from(Chapter).where(Chapter.project_id == project_id)) or 0)
            statement = select(Chapter).where(Chapter.project_id == project_id)
            if after is not None:
                ordinal, chapter_id = after
                statement = statement.where(
                    (Chapter.ordinal > ordinal) | ((Chapter.ordinal == ordinal) & (Chapter.id > chapter_id))
                )
            chapters = session.scalars(statement.order_by(Chapter.ordinal.asc(), Chapter.id.asc()).limit(bounded_limit + 1)).all()
            visible = tuple(chapters[:bounded_limit])
            summaries = tuple(_chapter_summary(session, chapter) for chapter in visible)
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


def _chapter_summary(session, chapter: Chapter) -> ChapterSummary:
    progress = _progress(session, chapter.id)
    source_hash = session.scalar(
        select(SourceRevision.normalized_sha256).where(SourceRevision.id == chapter.active_source_revision_id)
    )
    translation = None
    if chapter.approved_translation_run_id:
        translation = session.get(TranslationRun, chapter.approved_translation_run_id)
    issues = int(session.scalar(select(func.count()).select_from(QaIssue).where(QaIssue.chapter_id == chapter.id, QaIssue.status == "OPEN")) or 0)
    return ChapterSummary(
        id=chapter.id,
        project_id=chapter.project_id,
        ordinal=chapter.ordinal,
        source_title=chapter.source_title,
        translated_title=chapter.translated_title,
        state=ChapterState(chapter.state),
        progress=progress,
        cost=ChapterCost(
            estimated_vnd=translation.estimated_cost_vnd if translation else None,
            actual_vnd=translation.actual_cost_vnd if translation else None,
        ),
        issues=issues,
        hashes=ChapterHashes(
            source_sha256=source_hash,
            translation_sha256=translation.translation_text_sha256 if translation else None,
        ),
    )


def _progress(session, chapter_id: str) -> ChapterProgress:
    jobs: Sequence[Job] = session.scalars(select(Job).where(Job.chapter_id == chapter_id)).all()
    if not jobs:
        return ChapterProgress(current=0, total=0)
    current = sum(1 for job in jobs if job.status in {"SUCCEEDED", "FAILED", "CANCELED", "BLOCKED_BUDGET", "BILLING_UNKNOWN"})
    return ChapterProgress(current=current, total=len(jobs))
