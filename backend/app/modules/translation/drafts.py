"""Segmented workspace drafts with optimistic concurrency (task U04).

A draft is scoped to (project, chapter, base source revision) and stores
per-segment text keyed by stable source segment id. Saving uses
compare-and-swap on ``expected_revision``:

- no existing draft + ``expected_revision`` None/0 -> create revision 1;
- existing draft + matching revision -> write revision+1;
- stale/absent expectation -> ``DraftConflict`` and nothing is written.

Drafts never touch approved translations: the service only writes
``workspace_drafts`` and validates that edited keys belong to the base
revision's segments.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import new_id
from app.db.models import Chapter, SourceRevision, SourceSegment, WorkspaceDraft


MAX_DRAFT_BYTES = 1_048_576


class DraftConflict(Exception):
    def __init__(self, code: str = "DRAFT_REVISION_CONFLICT") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DraftView:
    id: str
    project_id: str
    chapter_id: str
    base_revision_id: str
    content: dict[str, str]
    revision: int


def restore_draft(
    session: Session, chapter_id: str, base_revision_id: str
) -> DraftView | None:
    row = _load(session, chapter_id, base_revision_id)
    return _view(row) if row is not None else None


def save_draft(
    session: Session,
    chapter_id: str,
    base_revision_id: str,
    content: dict[str, str],
    *,
    expected_revision: int | None,
    id_factory: Callable[[], str] = new_id,
) -> DraftView:
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise ValueError("CHAPTER_NOT_FOUND")
    revision = session.get(SourceRevision, base_revision_id)
    if revision is None or revision.chapter_id != chapter.id:
        raise ValueError("BASE_REVISION_NOT_FOUND")

    normalized = _normalize_content(content)
    _require_known_segments(session, base_revision_id, normalized)

    row = _load(session, chapter_id, base_revision_id)
    if row is None:
        if expected_revision not in (None, 0):
            raise DraftConflict()
        row = WorkspaceDraft(
            id=id_factory(),
            project_id=chapter.project_id,
            chapter_id=chapter.id,
            base_revision_id=base_revision_id,
            content_json=normalized,
            revision=1,
        )
        session.add(row)
    else:
        if expected_revision != row.revision:
            raise DraftConflict()
        row.content_json = normalized
        row.revision = row.revision + 1
    session.flush()
    session.commit()
    return _view(row)


def _load(session: Session, chapter_id: str, base_revision_id: str) -> WorkspaceDraft | None:
    return session.scalar(
        select(WorkspaceDraft).where(
            WorkspaceDraft.chapter_id == chapter_id,
            WorkspaceDraft.base_revision_id == base_revision_id,
        )
    )


def _normalize_content(content: dict[str, str]) -> dict[str, str]:
    if not isinstance(content, dict):
        raise ValueError("DRAFT_CONTENT_INVALID")
    normalized: dict[str, str] = {}
    for key, value in content.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("DRAFT_SEGMENT_ID_INVALID")
        if not isinstance(value, str):
            raise ValueError("DRAFT_TEXT_INVALID")
        normalized[key.strip()] = value
    encoded = json.dumps(normalized, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_DRAFT_BYTES:
        raise ValueError("DRAFT_TOO_LARGE")
    return normalized


def _require_known_segments(
    session: Session, base_revision_id: str, content: dict[str, str]
) -> None:
    if not content:
        return
    known = set(
        session.scalars(
            select(SourceSegment.id).where(SourceSegment.source_revision_id == base_revision_id)
        ).all()
    )
    unknown = set(content) - known
    if unknown:
        raise ValueError("DRAFT_SEGMENT_UNKNOWN")


def _view(row: WorkspaceDraft) -> DraftView:
    return DraftView(
        id=row.id,
        project_id=row.project_id,
        chapter_id=row.chapter_id,
        base_revision_id=row.base_revision_id,
        content=dict(row.content_json or {}),
        revision=row.revision,
    )
