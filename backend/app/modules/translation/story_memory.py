from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import StoryMemoryEntry as StoryMemoryRow


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


class StoryMemoryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def memory_for(self, project_id: str, ordinal: int) -> tuple[StoryMemoryEntry, ...]:
        rows = self.session.scalars(
            select(StoryMemoryRow)
            .where(
                StoryMemoryRow.project_id == project_id,
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
    )
