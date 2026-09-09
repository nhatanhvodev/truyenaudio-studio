from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import new_id
from app.db.models import Chapter, GlossaryEntry, Project, SourceRevision, SourceSegment
from app.modules.projects.invalidation import Change, InvalidationGraph


@dataclass(frozen=True)
class GlossaryCommand:
    source_term: str
    target_term: str
    reading: str | None = None
    category: str | None = None
    gender: str | None = None
    addressing_notes: str | None = None
    is_locked: bool = False
    description: str | None = None
    forbidden_forms: tuple[str, ...] = ()
    evidence: str | None = None
    scope_from_ordinal: int | None = None
    scope_to_ordinal: int | None = None


@dataclass(frozen=True)
class GlossaryEntryView:
    id: str
    source_term: str
    target_term: str
    reading: str | None
    category: str | None
    gender: str | None
    addressing_notes: str | None
    is_locked: bool
    revision_no: int
    supersedes_id: str | None
    description: str | None
    forbidden_forms: tuple[str, ...]
    evidence: str | None
    scope_from_ordinal: int | None
    scope_to_ordinal: int | None


@dataclass(frozen=True)
class GlossaryRevision:
    entries: tuple[GlossaryEntryView, ...]
    sha256: str


@dataclass(frozen=True)
class GlossaryUpsertResult:
    revision: GlossaryRevision
    affected_source_segment_ids: tuple[str, ...]
    invalidated: tuple[str, ...] = ()


@dataclass(frozen=True)
class LockedGlossaryRule:
    """One in-scope locked glossary rule for a chapter.

    ``source_term``/``target_term`` are the provider-facing locked pair;
    ``forbidden_forms`` are target renderings QA must flag for this source
    term. Chapter scope is already resolved before this rule is produced.
    """

    source_term: str
    target_term: str
    forbidden_forms: tuple[str, ...] = ()


class GlossaryService:
    def __init__(
        self, session: Session, id_factory: Callable[[], str] = new_id
    ) -> None:
        self.session = session
        self.id_factory = id_factory

    def upsert(self, project_id: str, command: GlossaryCommand) -> GlossaryUpsertResult:
        project = self.session.get(Project, project_id)
        if project is None:
            raise ValueError("PROJECT_NOT_FOUND")
        source_term = command.source_term.strip()
        target_term = command.target_term.strip()
        if not source_term:
            raise ValueError("GLOSSARY_SOURCE_TERM_REQUIRED")
        if not target_term:
            raise ValueError("GLOSSARY_TARGET_TERM_REQUIRED")
        _validate_scope(command)
        forbidden_forms = _normalize_forbidden_forms(command.forbidden_forms)
        if target_term in forbidden_forms:
            raise ValueError("GLOSSARY_CONFLICT_INTERNAL")

        active_entry = _active_entry_for(self.session, project_id, source_term)
        normalized = _normalized_command(
            command, source_term, target_term, forbidden_forms
        )
        changed = active_entry is None or _entry_payload(active_entry) != normalized
        if changed:
            revision_no = (
                self.session.scalar(
                    select(func.max(GlossaryEntry.revision_no)).where(
                        GlossaryEntry.project_id == project_id,
                        GlossaryEntry.source_term == source_term,
                    )
                )
                or 0
            ) + 1
            self.session.add(
                GlossaryEntry(
                    id=self.id_factory(),
                    project_id=project_id,
                    source_term=source_term,
                    target_term=target_term,
                    reading=command.reading,
                    category=command.category,
                    gender=command.gender,
                    addressing_notes=command.addressing_notes,
                    is_locked=command.is_locked,
                    revision_no=revision_no,
                    supersedes_id=active_entry.id if active_entry is not None else None,
                    description=command.description,
                    forbidden_forms=list(forbidden_forms) or None,
                    evidence=command.evidence,
                    scope_from_ordinal=command.scope_from_ordinal,
                    scope_to_ordinal=command.scope_to_ordinal,
                )
            )
            self.session.flush()

        revision = active_glossary(self.session, project_id)
        affected = _affected_segment_ids(
            self.session,
            project_id,
            source_term,
            command.scope_from_ordinal,
            command.scope_to_ordinal,
        )
        invalidated: tuple[str, ...] = ()
        if changed:
            chapter_ids = _affected_chapter_ids(
                self.session,
                project_id,
                source_term,
                command.scope_from_ordinal,
                command.scope_to_ordinal,
            )
            if chapter_ids:
                plan = InvalidationGraph(self.session).plan(Change.glossary(*chapter_ids))
                invalidated = plan.invalidated
        self.session.commit()
        return GlossaryUpsertResult(revision, affected, invalidated)


def active_glossary(session: Session, project_id: str) -> GlossaryRevision:
    entries = tuple(
        _entry_view(entry) for entry in _active_entries(session, project_id)
    )
    payload = [_entry_json(entry) for entry in entries]
    sha256 = hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return GlossaryRevision(entries, sha256)


def locked_rules_for_chapter(
    session: Session, project_id: str, chapter_ordinal: int
) -> tuple[LockedGlossaryRule, ...]:
    """In-scope locked rules for one chapter.

    Only entries whose chapter scope covers ``chapter_ordinal`` are returned,
    so a scoped term is never applied outside its scope.
    """
    return tuple(
        LockedGlossaryRule(
            source_term=entry.source_term,
            target_term=entry.target_term,
            forbidden_forms=tuple(entry.forbidden_forms or ()),
        )
        for entry in _active_entries(session, project_id)
        if entry.is_locked and _scope_covers(entry, chapter_ordinal)
    )


def _active_entries(session: Session, project_id: str) -> tuple[GlossaryEntry, ...]:
    superseded = (
        select(GlossaryEntry.supersedes_id)
        .where(
            GlossaryEntry.project_id == project_id,
            GlossaryEntry.supersedes_id.is_not(None),
        )
        .subquery()
    )
    return tuple(
        session.scalars(
            select(GlossaryEntry)
            .where(
                GlossaryEntry.project_id == project_id,
                GlossaryEntry.id.not_in(select(superseded.c.supersedes_id)),
            )
            .order_by(GlossaryEntry.source_term, GlossaryEntry.id)
        ).all()
    )


def _active_entry_for(
    session: Session, project_id: str, source_term: str
) -> GlossaryEntry | None:
    superseded = (
        select(GlossaryEntry.supersedes_id)
        .where(
            GlossaryEntry.project_id == project_id,
            GlossaryEntry.supersedes_id.is_not(None),
        )
        .subquery()
    )
    return session.scalar(
        select(GlossaryEntry)
        .where(
            GlossaryEntry.project_id == project_id,
            GlossaryEntry.source_term == source_term,
            GlossaryEntry.id.not_in(select(superseded.c.supersedes_id)),
        )
        .order_by(GlossaryEntry.revision_no.desc(), GlossaryEntry.id.desc())
    )


def _affected_segment_ids(
    session: Session,
    project_id: str,
    source_term: str,
    scope_from_ordinal: int | None = None,
    scope_to_ordinal: int | None = None,
) -> tuple[str, ...]:
    query = (
        select(SourceSegment.id)
        .join(SourceRevision, SourceRevision.id == SourceSegment.source_revision_id)
        .join(Chapter, Chapter.id == SourceRevision.chapter_id)
        .where(
            Chapter.project_id == project_id,
            Chapter.active_source_revision_id == SourceRevision.id,
            SourceSegment.source_text.contains(source_term),
        )
    )
    if scope_from_ordinal is not None:
        query = query.where(Chapter.ordinal >= scope_from_ordinal)
    if scope_to_ordinal is not None:
        query = query.where(Chapter.ordinal <= scope_to_ordinal)
    return tuple(
        session.scalars(
            query.order_by(Chapter.ordinal, SourceSegment.segment_index, SourceSegment.id)
        ).all()
    )


def _affected_chapter_ids(
    session: Session,
    project_id: str,
    source_term: str,
    scope_from_ordinal: int | None = None,
    scope_to_ordinal: int | None = None,
) -> tuple[str, ...]:
    """Chapters whose active source actually contains the term in scope."""
    query = (
        select(Chapter.id)
        .join(SourceRevision, SourceRevision.id == Chapter.active_source_revision_id)
        .join(SourceSegment, SourceSegment.source_revision_id == SourceRevision.id)
        .where(
            Chapter.project_id == project_id,
            SourceSegment.source_text.contains(source_term),
        )
        .distinct()
    )
    if scope_from_ordinal is not None:
        query = query.where(Chapter.ordinal >= scope_from_ordinal)
    if scope_to_ordinal is not None:
        query = query.where(Chapter.ordinal <= scope_to_ordinal)
    return tuple(session.scalars(query.order_by(Chapter.ordinal)).all())


def _validate_scope(command: GlossaryCommand) -> None:
    start = command.scope_from_ordinal
    end = command.scope_to_ordinal
    if start is not None and start < 1:
        raise ValueError("GLOSSARY_SCOPE_INVALID")
    if end is not None and end < 1:
        raise ValueError("GLOSSARY_SCOPE_INVALID")
    if start is not None and end is not None and start > end:
        raise ValueError("GLOSSARY_SCOPE_INVALID")


def _scope_covers(entry: GlossaryEntry, chapter_ordinal: int) -> bool:
    if entry.scope_from_ordinal is not None and chapter_ordinal < entry.scope_from_ordinal:
        return False
    if entry.scope_to_ordinal is not None and chapter_ordinal > entry.scope_to_ordinal:
        return False
    return True


def _normalize_forbidden_forms(forms: tuple[str, ...]) -> tuple[str, ...]:
    seen: list[str] = []
    for form in forms:
        cleaned = form.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.append(cleaned)
    return tuple(seen)


def _normalized_command(
    command: GlossaryCommand,
    source_term: str,
    target_term: str,
    forbidden_forms: tuple[str, ...],
) -> dict[str, object]:
    return {
        "source_term": source_term,
        "target_term": target_term,
        "reading": command.reading,
        "category": command.category,
        "gender": command.gender,
        "addressing_notes": command.addressing_notes,
        "is_locked": command.is_locked,
        "description": command.description,
        "forbidden_forms": list(forbidden_forms) or None,
        "evidence": command.evidence,
        "scope_from_ordinal": command.scope_from_ordinal,
        "scope_to_ordinal": command.scope_to_ordinal,
    }


def _entry_payload(entry: GlossaryEntry) -> dict[str, object]:
    return {
        "source_term": entry.source_term,
        "target_term": entry.target_term,
        "reading": entry.reading,
        "category": entry.category,
        "gender": entry.gender,
        "addressing_notes": entry.addressing_notes,
        "is_locked": entry.is_locked,
        "description": entry.description,
        "forbidden_forms": entry.forbidden_forms,
        "evidence": entry.evidence,
        "scope_from_ordinal": entry.scope_from_ordinal,
        "scope_to_ordinal": entry.scope_to_ordinal,
    }


def _entry_view(entry: GlossaryEntry) -> GlossaryEntryView:
    return GlossaryEntryView(
        id=entry.id,
        source_term=entry.source_term,
        target_term=entry.target_term,
        reading=entry.reading,
        category=entry.category,
        gender=entry.gender,
        addressing_notes=entry.addressing_notes,
        is_locked=entry.is_locked,
        revision_no=entry.revision_no,
        supersedes_id=entry.supersedes_id,
        description=entry.description,
        forbidden_forms=tuple(entry.forbidden_forms or ()),
        evidence=entry.evidence,
        scope_from_ordinal=entry.scope_from_ordinal,
        scope_to_ordinal=entry.scope_to_ordinal,
    )


def _entry_json(entry: GlossaryEntryView) -> dict[str, object]:
    return {
        "addressing_notes": entry.addressing_notes,
        "category": entry.category,
        "description": entry.description,
        "evidence": entry.evidence,
        "forbidden_forms": list(entry.forbidden_forms) or None,
        "gender": entry.gender,
        "id": entry.id,
        "is_locked": entry.is_locked,
        "reading": entry.reading,
        "revision_no": entry.revision_no,
        "scope_from_ordinal": entry.scope_from_ordinal,
        "scope_to_ordinal": entry.scope_to_ordinal,
        "source_term": entry.source_term,
        "supersedes_id": entry.supersedes_id,
        "target_term": entry.target_term,
    }
