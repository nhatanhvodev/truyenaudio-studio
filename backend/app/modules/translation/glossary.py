from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import new_id
from app.db.models import Chapter, GlossaryEntry, Project, SourceRevision, SourceSegment


@dataclass(frozen=True)
class GlossaryCommand:
    source_term: str
    target_term: str
    reading: str | None
    category: str | None
    gender: str | None
    addressing_notes: str | None
    is_locked: bool


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


@dataclass(frozen=True)
class GlossaryRevision:
    entries: tuple[GlossaryEntryView, ...]
    sha256: str


@dataclass(frozen=True)
class GlossaryUpsertResult:
    revision: GlossaryRevision
    affected_source_segment_ids: tuple[str, ...]


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

        active_entry = _active_entry_for(self.session, project_id, source_term)
        normalized = _normalized_command(command, source_term, target_term)
        if active_entry is None or _entry_payload(active_entry) != normalized:
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
                )
            )
            self.session.flush()

        revision = active_glossary(self.session, project_id)
        affected = _affected_segment_ids(self.session, project_id, source_term)
        self.session.commit()
        return GlossaryUpsertResult(revision, affected)


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
    session: Session, project_id: str, source_term: str
) -> tuple[str, ...]:
    return tuple(
        session.scalars(
            select(SourceSegment.id)
            .join(SourceRevision, SourceRevision.id == SourceSegment.source_revision_id)
            .join(Chapter, Chapter.id == SourceRevision.chapter_id)
            .where(
                Chapter.project_id == project_id,
                Chapter.active_source_revision_id == SourceRevision.id,
                SourceSegment.source_text.contains(source_term),
            )
            .order_by(Chapter.ordinal, SourceSegment.segment_index, SourceSegment.id)
        ).all()
    )


def _normalized_command(
    command: GlossaryCommand, source_term: str, target_term: str
) -> dict[str, object]:
    return {
        "source_term": source_term,
        "target_term": target_term,
        "reading": command.reading,
        "category": command.category,
        "gender": command.gender,
        "addressing_notes": command.addressing_notes,
        "is_locked": command.is_locked,
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
    )


def _entry_json(entry: GlossaryEntryView) -> dict[str, object]:
    return {
        "addressing_notes": entry.addressing_notes,
        "category": entry.category,
        "gender": entry.gender,
        "id": entry.id,
        "is_locked": entry.is_locked,
        "reading": entry.reading,
        "revision_no": entry.revision_no,
        "source_term": entry.source_term,
        "supersedes_id": entry.supersedes_id,
        "target_term": entry.target_term,
    }
