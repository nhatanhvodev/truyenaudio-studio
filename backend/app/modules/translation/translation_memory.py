"""Approved-pair translation memory with exact-match reuse (task C03).

Only translations from an APPROVED run are recorded. A row is reused for a
new request only when the full fingerprint matches — project, source hash,
source/target language, glossary hash, style revision — and the approving run
is still APPROVED (a superseded run makes its rows ineligible at read time, so
no background sweep is needed). Fuzzy suggestions are out of scope here: they
are surfaced by the UI/context engine, never applied automatically.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.contracts import RunStatus, new_id
from app.db.models import (
    Chapter,
    Project,
    SourceSegment,
    TranslationMemoryEntry,
    TranslationRun,
    TranslationSegment,
)


@dataclass(frozen=True)
class TmMatch:
    source_text: str
    target_text: str
    source_language: str
    target_language: str


def record_approved_run(
    session: Session,
    run_id: str,
    id_factory: Callable[[], str] = new_id,
) -> int:
    """Upsert memory rows for every segment of an APPROVED run.

    One READY row per (project, source_hash, language pair, glossary hash,
    style revision); recording a newer approved run replaces the older row for
    the same fingerprint. Returns the number of recorded pairs.
    """
    run = session.get(TranslationRun, run_id)
    if run is None:
        raise ValueError("TRANSLATION_RUN_NOT_FOUND")
    if run.status != RunStatus.APPROVED.value:
        raise ValueError("TRANSLATION_RUN_NOT_APPROVED")
    chapter = session.get(Chapter, run.chapter_id)
    if chapter is None:
        raise ValueError("CHAPTER_NOT_FOUND")
    project = session.get(Project, chapter.project_id)
    if project is None:
        raise ValueError("PROJECT_NOT_FOUND")

    recorded = 0
    for segment in session.scalars(
        select(TranslationSegment).where(TranslationSegment.translation_run_id == run.id)
    ):
        source = session.get(SourceSegment, segment.source_segment_id)
        if source is None:
            continue
        source_text = source.source_text.strip()
        target_text = (segment.target_text or "").strip()
        if not source_text or not target_text:
            continue
        source_hash = _sha(source_text)
        session.execute(
            delete(TranslationMemoryEntry).where(
                TranslationMemoryEntry.project_id == project.id,
                TranslationMemoryEntry.source_hash == source_hash,
                TranslationMemoryEntry.source_language == project.default_language,
                TranslationMemoryEntry.target_language == project.target_language,
                TranslationMemoryEntry.glossary_hash == run.glossary_revision_hash,
                TranslationMemoryEntry.style_revision_id.is_(None),
                TranslationMemoryEntry.approved_run_id != run.id,
            )
        )
        session.add(
            TranslationMemoryEntry(
                id=id_factory(),
                project_id=project.id,
                source_hash=source_hash,
                source_text=source_text,
                target_text=target_text,
                source_language=project.default_language,
                target_language=project.target_language,
                style_revision_id=None,
                glossary_hash=run.glossary_revision_hash,
                approved_run_id=run.id,
            )
        )
        recorded += 1
    if recorded:
        session.flush()
    return recorded


def exact_match(
    session: Session,
    *,
    project_id: str,
    source_text: str,
    source_language: str,
    target_language: str,
    glossary_hash: str | None,
    style_revision_id: str | None = None,
) -> TmMatch | None:
    """Exact-match lookup restricted to rows whose approving run is APPROVED."""
    source_text = source_text.strip()
    if not source_text:
        return None
    row = session.scalar(
        select(TranslationMemoryEntry)
        .join(TranslationRun, TranslationRun.id == TranslationMemoryEntry.approved_run_id)
        .where(
            TranslationMemoryEntry.project_id == project_id,
            TranslationMemoryEntry.source_hash == _sha(source_text),
            TranslationMemoryEntry.source_language == source_language,
            TranslationMemoryEntry.target_language == target_language,
            TranslationMemoryEntry.glossary_hash == glossary_hash,
            TranslationMemoryEntry.style_revision_id == style_revision_id,
            TranslationRun.status == RunStatus.APPROVED.value,
        )
        .order_by(TranslationMemoryEntry.updated_at.desc(), TranslationMemoryEntry.id.desc())
    )
    if row is None:
        return None
    return TmMatch(row.source_text, row.target_text, row.source_language, row.target_language)


def count_for_project(session: Session, project_id: str) -> int:
    return int(
        session.scalar(
            select(func.count(TranslationMemoryEntry.id)).where(
                TranslationMemoryEntry.project_id == project_id
            )
        )
        or 0
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
