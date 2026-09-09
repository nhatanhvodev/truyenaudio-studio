"""Chapter/entity story memory with candidate -> approved lifecycle (C05).

Memory rows carry an approval status. Only APPROVED rows are ever exposed to
translation context (candidates and rejected rows are excluded at read time,
so a candidate summary can never leak into a prompt). Approval requires
evidence: the APPROVED translation run that produced the summary plus at
least one source segment of that run. The canonical revision hash used by
TranslationRun covers APPROVED rows only and keeps the original field schema
so stored run hashes stay comparable.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import RunStatus, new_id
from app.db.models import Chapter, Project, SourceSegment, StoryMemoryEntry as StoryMemoryRow, TranslationRun

from collections.abc import Callable


STATUS_APPROVED = "APPROVED"
STATUS_CANDIDATE = "CANDIDATE"
STATUS_REJECTED = "REJECTED"
STATUS_STALE = "STALE"


@dataclass(frozen=True)
class StoryMemoryEntry:
    id: str
    project_id: str
    entity_key: str
    entity_type: str
    summary: str
    valid_from_ordinal: int
    valid_to_ordinal: int | None
    revision_no: int
    status: str
    source_run_id: str | None
    evidence_segment_ids: tuple[str, ...]


class StoryMemoryService:
    def __init__(self, session: Session, id_factory: Callable[[], str] = new_id) -> None:
        self.session = session
        self.id_factory = id_factory

    def memory_for(self, project_id: str, ordinal: int) -> tuple[StoryMemoryEntry, ...]:
        approved_runs = (
            select(TranslationRun.id)
            .where(TranslationRun.status == RunStatus.APPROVED.value)
            .scalar_subquery()
        )
        rows = self.session.scalars(
            select(StoryMemoryRow)
            .where(
                StoryMemoryRow.project_id == project_id,
                StoryMemoryRow.status == STATUS_APPROVED,
                (
                    StoryMemoryRow.source_run_id.is_(None)
                    | StoryMemoryRow.source_run_id.in_(approved_runs)
                ),
                StoryMemoryRow.valid_from_ordinal <= ordinal,
                (
                    StoryMemoryRow.valid_to_ordinal.is_(None)
                    | (StoryMemoryRow.valid_to_ordinal >= ordinal)
                ),
            )
            .order_by(StoryMemoryRow.entity_key, StoryMemoryRow.revision_no, StoryMemoryRow.id)
        ).all()
        return tuple(_view(row) for row in rows)

    def summaries_for(self, project_id: str, ordinal: int) -> tuple[str, ...]:
        return tuple(entry.summary for entry in self.memory_for(project_id, ordinal))

    def hash_for(self, project_id: str, ordinal: int) -> str:
        entries = self.memory_for(project_id, ordinal)
        return hashlib.sha256(
            json.dumps(
                [
                    {
                        "entity_key": entry.entity_key,
                        "entity_type": entry.entity_type,
                        "summary": entry.summary,
                        "valid_from_ordinal": entry.valid_from_ordinal,
                        "valid_to_ordinal": entry.valid_to_ordinal,
                        "revision_no": entry.revision_no,
                    }
                    for entry in entries
                ],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def candidates_for(self, project_id: str) -> tuple[StoryMemoryEntry, ...]:
        rows = self.session.scalars(
            select(StoryMemoryRow)
            .where(
                StoryMemoryRow.project_id == project_id,
                StoryMemoryRow.status == STATUS_CANDIDATE,
            )
            .order_by(StoryMemoryRow.created_at, StoryMemoryRow.id)
        ).all()
        return tuple(_view(row) for row in rows)

    def create_candidate(
        self,
        project_id: str,
        *,
        entity_key: str,
        entity_type: str,
        summary: str,
        valid_from_ordinal: int,
        valid_to_ordinal: int | None = None,
    ) -> StoryMemoryEntry:
        project = self.session.get(Project, project_id)
        if project is None:
            raise ValueError("PROJECT_NOT_FOUND")
        entity_key = entity_key.strip()
        summary = summary.strip()
        if not entity_key:
            raise ValueError("MEMORY_ENTITY_REQUIRED")
        if not summary:
            raise ValueError("MEMORY_SUMMARY_REQUIRED")
        if valid_from_ordinal < 1:
            raise ValueError("MEMORY_ORDINAL_INVALID")
        if valid_to_ordinal is not None and valid_to_ordinal < valid_from_ordinal:
            raise ValueError("MEMORY_ORDINAL_INVALID")
        existing = _latest_revision(self.session, project_id, entity_key)
        revision_no = (existing.revision_no if existing is not None else 0) + 1
        row = StoryMemoryRow(
            id=self.id_factory(),
            project_id=project_id,
            entity_key=entity_key,
            entity_type=entity_type.strip() or "FACT",
            summary=summary,
            valid_from_ordinal=valid_from_ordinal,
            valid_to_ordinal=valid_to_ordinal,
            revision_no=revision_no,
            status=STATUS_CANDIDATE,
        )
        self.session.add(row)
        self.session.flush()
        self.session.commit()
        return _view(row)

    def approve(
        self,
        project_id: str,
        memory_id: str,
        *,
        source_run_id: str,
        evidence_segment_ids: tuple[str, ...],
    ) -> StoryMemoryEntry:
        project = self.session.get(Project, project_id)
        if project is None:
            raise ValueError("PROJECT_NOT_FOUND")
        row = self.session.get(StoryMemoryRow, memory_id)
        if row is None or row.project_id != project_id:
            raise ValueError("MEMORY_ENTRY_NOT_FOUND")
        if row.status != STATUS_CANDIDATE:
            raise ValueError("MEMORY_ENTRY_NOT_CANDIDATE")
        run = self.session.get(TranslationRun, source_run_id)
        if run is None:
            raise ValueError("MEMORY_EVIDENCE_RUN_NOT_FOUND")
        chapter = self.session.get(Chapter, run.chapter_id)
        if chapter is None or chapter.project_id != project_id:
            raise ValueError("MEMORY_EVIDENCE_RUN_NOT_FOUND")
        if run.status != RunStatus.APPROVED.value:
            raise ValueError("MEMORY_EVIDENCE_RUN_NOT_APPROVED")
        evidence_segment_ids = tuple(dict.fromkeys(segment_id for segment_id in evidence_segment_ids if segment_id))
        if not evidence_segment_ids:
            raise ValueError("MEMORY_EVIDENCE_SEGMENTS_REQUIRED")
        existing = set(
            self.session.scalars(
                select(SourceSegment.id).where(
                    SourceSegment.source_revision_id == run.source_revision_id,
                    SourceSegment.id.in_(evidence_segment_ids),
                )
            ).all()
        )
        if existing != set(evidence_segment_ids):
            raise ValueError("MEMORY_EVIDENCE_SEGMENTS_INVALID")
        row.status = STATUS_APPROVED
        row.source_run_id = run.id
        row.evidence_segment_ids_json = list(evidence_segment_ids)
        self.session.flush()
        self.session.commit()
        return _view(row)

    def reject(self, project_id: str, memory_id: str) -> StoryMemoryEntry:
        row = self.session.get(StoryMemoryRow, memory_id)
        if row is None or row.project_id != project_id:
            raise ValueError("MEMORY_ENTRY_NOT_FOUND")
        if row.status != STATUS_CANDIDATE:
            raise ValueError("MEMORY_ENTRY_NOT_CANDIDATE")
        row.status = STATUS_REJECTED
        self.session.flush()
        self.session.commit()
        return _view(row)


def _latest_revision(session: Session, project_id: str, entity_key: str) -> StoryMemoryRow | None:
    return session.scalar(
        select(StoryMemoryRow)
        .where(
            StoryMemoryRow.project_id == project_id,
            StoryMemoryRow.entity_key == entity_key,
        )
        .order_by(StoryMemoryRow.revision_no.desc(), StoryMemoryRow.id.desc())
        .limit(1)
    )


def _view(row: StoryMemoryRow) -> StoryMemoryEntry:
    return StoryMemoryEntry(
        id=row.id,
        project_id=row.project_id,
        entity_key=row.entity_key,
        entity_type=row.entity_type,
        summary=row.summary,
        valid_from_ordinal=row.valid_from_ordinal,
        valid_to_ordinal=row.valid_to_ordinal,
        revision_no=row.revision_no,
        status=row.status,
        source_run_id=row.source_run_id,
        evidence_segment_ids=tuple(row.evidence_segment_ids_json or ()),
    )
