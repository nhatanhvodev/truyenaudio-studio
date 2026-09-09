"""Characters and directed addressing relationships (task C04).

A character is a stable identity; name/alias/gender/role facts live in
immutable CharacterRevision rows (UNIQUE(character_id, revision_no)).
Approval requires evidence (a source revision plus at least one source
segment under it). Gender is nullable and never guessed. Alias resolution
returns an ambiguity error instead of merging characters by name.
Relationships are directed (speaker -> addressee), scoped by chapter
ordinal interval, and only allowed between approved characters of the same
project. Character is never conflated with VoiceRole.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import new_id
from app.db.models import (
    Chapter,
    Character,
    CharacterRelationship,
    CharacterRevision,
    Project,
    SourceRevision,
    SourceSegment,
)


ENTITY_TYPES = frozenset({"PERSON", "ORGANIZATION", "OTHER"})
GENDERS = frozenset({"MALE", "FEMALE", "UNKNOWN"})
STATUSES = frozenset({"CANDIDATE", "APPROVED", "STALE"})
RELATIONSHIP_STATUS = "ACTIVE"


class CharacterAliasAmbiguous(Exception):
    pass


@dataclass(frozen=True)
class CharacterView:
    character_id: str
    revision_id: str
    revision_no: int
    supersedes_id: str | None
    canonical_name: str
    aliases: tuple[str, ...]
    entity_type: str
    role: str | None
    gender: str | None
    status: str
    evidence_source_revision_id: str | None
    evidence_segment_ids: tuple[str, ...]
    sha256: str


@dataclass(frozen=True)
class RelationshipCommand:
    from_character_id: str
    to_character_id: str
    from_ordinal: int
    to_ordinal: int | None
    addressing: dict[str, str] | None = None
    evidence_source_revision_id: str | None = None


@dataclass(frozen=True)
class RelationshipView:
    id: str
    from_character_id: str
    to_character_id: str
    from_ordinal: int
    to_ordinal: int | None
    addressing: dict[str, str]
    evidence_source_revision_id: str | None
    status: str


class CharacterService:
    def __init__(self, session: Session, id_factory: Callable[[], str] = new_id) -> None:
        self.session = session
        self.id_factory = id_factory

    def create_or_revise(
        self,
        project_id: str,
        *,
        canonical_name: str,
        entity_type: str,
        aliases: tuple[str, ...] = (),
        role: str | None = None,
        gender: str | None = None,
        character_id: str | None = None,
        status: str = "CANDIDATE",
        evidence_source_revision_id: str | None = None,
        evidence_segment_ids: tuple[str, ...] = (),
    ) -> CharacterView:
        self._require_project(project_id)
        normalized = self._normalize_facts(
            project_id,
            canonical_name=canonical_name,
            entity_type=entity_type,
            aliases=aliases,
            role=role,
            gender=gender,
            status=status,
            evidence_source_revision_id=evidence_source_revision_id,
            evidence_segment_ids=evidence_segment_ids,
        )
        character = self._character_for(project_id, character_id) if character_id else None
        active = _active_revision(self.session, character.id) if character is not None else None
        if active is not None and _facts(active) == normalized:
            return _view(active)
        if character is not None and active is not None:
            revision_no = active.revision_no + 1
        elif character is not None:
            last_no = self.session.scalar(
                select(CharacterRevision.revision_no)
                .where(CharacterRevision.character_id == character.id)
                .order_by(CharacterRevision.revision_no.desc())
                .limit(1)
            )
            revision_no = (last_no or 0) + 1
        else:
            revision_no = 1
        if character is None:
            character = Character(id=self.id_factory(), project_id=project_id)
            self.session.add(character)
            self.session.flush()
        self.session.add(
            CharacterRevision(
                id=self.id_factory(),
                character_id=character.id,
                revision_no=revision_no,
                supersedes_id=active.id if active is not None else None,
                canonical_name=normalized["canonical_name"],
                aliases_json=list(normalized["aliases"]) or None,
                entity_type=normalized["entity_type"],
                role=normalized["role"],
                gender=normalized["gender"],
                status=normalized["status"],
                evidence_source_revision_id=normalized["evidence_source_revision_id"],
                evidence_segment_ids_json=list(normalized["evidence_segment_ids"]) or None,
            )
        )
        self.session.flush()
        self.session.commit()
        return _view(
            self.session.scalar(
                select(CharacterRevision)
                .where(CharacterRevision.character_id == character.id)
                .order_by(CharacterRevision.revision_no.desc(), CharacterRevision.id.desc())
            )
        )

    def approve(
        self,
        project_id: str,
        character_id: str,
        *,
        evidence_source_revision_id: str,
        evidence_segment_ids: tuple[str, ...],
    ) -> CharacterView:
        character = self._character_for(project_id, character_id)
        active = _active_revision(self.session, character.id)
        if active is None:
            raise ValueError("CHARACTER_REVISION_NOT_FOUND")
        if active.status == "APPROVED":
            return _view(active)
        normalized = self._normalize_facts(
            project_id,
            canonical_name=active.canonical_name,
            entity_type=active.entity_type,
            aliases=tuple(active.aliases_json or ()),
            role=active.role,
            gender=active.gender,
            status="APPROVED",
            evidence_source_revision_id=evidence_source_revision_id,
            evidence_segment_ids=evidence_segment_ids,
        )
        revision_no = active.revision_no + 1
        self.session.add(
            CharacterRevision(
                id=self.id_factory(),
                character_id=character.id,
                revision_no=revision_no,
                supersedes_id=active.id,
                canonical_name=normalized["canonical_name"],
                aliases_json=list(normalized["aliases"]) or None,
                entity_type=normalized["entity_type"],
                role=normalized["role"],
                gender=normalized["gender"],
                status="APPROVED",
                evidence_source_revision_id=normalized["evidence_source_revision_id"],
                evidence_segment_ids_json=list(normalized["evidence_segment_ids"]) or None,
            )
        )
        self.session.flush()
        self.session.commit()
        return _view(
            self.session.scalar(
                select(CharacterRevision)
                .where(CharacterRevision.character_id == character.id)
                .order_by(CharacterRevision.revision_no.desc(), CharacterRevision.id.desc())
            )
        )

    def resolve_alias(
        self, project_id: str, alias: str, *, require_approved: bool = True
    ) -> CharacterView:
        needle = _normalize_text(alias)
        if not needle:
            raise ValueError("CHARACTER_ALIAS_REQUIRED")
        matches: list[CharacterView] = []
        for character in self.session.scalars(
            select(Character).where(Character.project_id == project_id)
        ):
            revision = _active_revision(self.session, character.id)
            if revision is None:
                continue
            if require_approved and revision.status != "APPROVED":
                continue
            names = [revision.canonical_name, *(revision.aliases_json or [])]
            if any(_normalize_text(name) == needle for name in names if name):
                matches.append(_view(revision))
        if not matches:
            raise ValueError("CHARACTER_NOT_FOUND")
        by_character = {view.character_id: view for view in matches}
        if len(by_character) > 1:
            raise CharacterAliasAmbiguous(
                "CHARACTER_ALIAS_AMBIGUOUS:" + ",".join(sorted(by_character))
            )
        return next(iter(by_character.values()))

    def active_characters(self, project_id: str) -> tuple[CharacterView, ...]:
        views: list[CharacterView] = []
        for character in self.session.scalars(
            select(Character).where(Character.project_id == project_id)
        ):
            revision = _active_revision(self.session, character.id)
            if revision is not None:
                views.append(_view(revision))
        return tuple(
            sorted(views, key=lambda view: (view.canonical_name.casefold(), view.character_id))
        )

    def add_relationship(
        self, project_id: str, command: RelationshipCommand
    ) -> RelationshipView:
        self._require_project(project_id)
        if command.from_character_id == command.to_character_id:
            raise ValueError("CHARACTER_RELATIONSHIP_SELF")
        if command.from_ordinal < 1:
            raise ValueError("CHARACTER_RELATIONSHIP_ORDINAL_INVALID")
        if (
            command.to_ordinal is not None
            and command.to_ordinal < command.from_ordinal
        ):
            raise ValueError("CHARACTER_RELATIONSHIP_ORDINAL_INVALID")
        if not _looks_like_string_map(command.addressing):
            raise ValueError("CHARACTER_RELATIONSHIP_ADDRESSING_INVALID")
        from_character = self._character_for(project_id, command.from_character_id)
        to_character = self._character_for(project_id, command.to_character_id)
        if from_character.project_id != project_id or to_character.project_id != project_id:
            raise ValueError("CHARACTER_RELATIONSHIP_CROSS_PROJECT")
        for character_id in (command.from_character_id, command.to_character_id):
            revision = _active_revision(self.session, character_id)
            if revision is None or revision.status != "APPROVED":
                raise ValueError("CHARACTER_NOT_APPROVED")
        if command.evidence_source_revision_id is not None:
            _validate_evidence(
                self.session, project_id, command.evidence_source_revision_id, ()
            )
        row = CharacterRelationship(
            id=self.id_factory(),
            project_id=project_id,
            from_character_id=command.from_character_id,
            to_character_id=command.to_character_id,
            from_ordinal=command.from_ordinal,
            to_ordinal=command.to_ordinal,
            addressing_json=dict(command.addressing) if command.addressing else None,
            evidence_source_revision_id=command.evidence_source_revision_id,
            status=RELATIONSHIP_STATUS,
        )
        self.session.add(row)
        self.session.flush()
        self.session.commit()
        return _relationship_view(row)

    def active_relationships(
        self, project_id: str, chapter_ordinal: int
    ) -> tuple[RelationshipView, ...]:
        rows = tuple(
            self.session.scalars(
                select(CharacterRelationship)
                .where(
                    CharacterRelationship.project_id == project_id,
                    CharacterRelationship.from_ordinal <= chapter_ordinal,
                    (CharacterRelationship.to_ordinal.is_(None))
                    | (CharacterRelationship.to_ordinal >= chapter_ordinal),
                )
                .order_by(CharacterRelationship.from_ordinal, CharacterRelationship.id)
            ).all()
        )
        return tuple(_relationship_view(row) for row in rows)

    # ---- internals ----

    def _require_project(self, project_id: str) -> None:
        if self.session.get(Project, project_id) is None:
            raise ValueError("PROJECT_NOT_FOUND")

    def _character_for(self, project_id: str, character_id: str) -> Character:
        character = self.session.get(Character, character_id)
        if character is None or character.project_id != project_id:
            raise ValueError("CHARACTER_NOT_FOUND")
        return character

    def _normalize_facts(
        self,
        project_id: str,
        *,
        canonical_name: str,
        entity_type: str,
        aliases: tuple[str, ...],
        role: str | None,
        gender: str | None,
        status: str,
        evidence_source_revision_id: str | None,
        evidence_segment_ids: tuple[str, ...],
    ) -> dict[str, object]:
        name = _normalize_text(canonical_name)
        if not name:
            raise ValueError("CHARACTER_NAME_REQUIRED")
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"CHARACTER_ENTITY_TYPE_INVALID:{entity_type}")
        if status not in STATUSES:
            raise ValueError(f"CHARACTER_STATUS_INVALID:{status}")
        if gender is not None and gender not in GENDERS:
            raise ValueError(f"CHARACTER_GENDER_INVALID:{gender}")
        normalized_aliases = tuple(dict.fromkeys(a for a in aliases if a and _normalize_text(a)))
        if role is not None:
            role = role.strip() or None
        evidence_segment_ids = tuple(
            segment_id
            for segment_id in dict.fromkeys(evidence_segment_ids)
            if segment_id
        )
        if status == "APPROVED":
            _validate_evidence(
                self.session,
                project_id,
                evidence_source_revision_id,
                evidence_segment_ids,
            )
        elif evidence_source_revision_id is not None or evidence_segment_ids:
            _validate_evidence(
                self.session,
                project_id,
                evidence_source_revision_id,
                evidence_segment_ids,
            )
        return {
            "canonical_name": name,
            "entity_type": entity_type,
            "aliases": normalized_aliases,
            "role": role,
            "gender": gender,
            "status": status,
            "evidence_source_revision_id": evidence_source_revision_id,
            "evidence_segment_ids": evidence_segment_ids,
        }


def _active_revision(session: Session, character_id: str) -> CharacterRevision | None:
    return session.scalar(
        select(CharacterRevision)
        .where(CharacterRevision.character_id == character_id)
        .order_by(CharacterRevision.revision_no.desc(), CharacterRevision.id.desc())
        .limit(1)
    )


def _validate_evidence(
    session: Session,
    project_id: str,
    evidence_source_revision_id: str | None,
    evidence_segment_ids: tuple[str, ...],
) -> None:
    if not evidence_source_revision_id:
        raise ValueError("CHARACTER_EVIDENCE_REQUIRED")
    if not evidence_segment_ids:
        raise ValueError("CHARACTER_EVIDENCE_SEGMENTS_REQUIRED")
    revision = session.get(SourceRevision, evidence_source_revision_id)
    if revision is None:
        raise ValueError("CHARACTER_EVIDENCE_SOURCE_NOT_FOUND")
    chapter = session.get(Chapter, revision.chapter_id)
    if chapter is None or chapter.project_id != project_id:
        raise ValueError("CHARACTER_EVIDENCE_SOURCE_NOT_FOUND")
    existing = set(
        session.scalars(
            select(SourceSegment.id).where(
                SourceSegment.source_revision_id == evidence_source_revision_id,
                SourceSegment.id.in_(evidence_segment_ids),
            )
        ).all()
    )
    if existing != set(evidence_segment_ids):
        raise ValueError("CHARACTER_EVIDENCE_SEGMENTS_INVALID")


def _facts(revision: CharacterRevision) -> dict[str, object]:
    return {
        "canonical_name": revision.canonical_name,
        "entity_type": revision.entity_type,
        "aliases": tuple(revision.aliases_json or ()),
        "role": revision.role,
        "gender": revision.gender,
        "status": revision.status,
        "evidence_source_revision_id": revision.evidence_source_revision_id,
        "evidence_segment_ids": tuple(revision.evidence_segment_ids_json or ()),
    }


def _view(revision: CharacterRevision) -> CharacterView:
    facts = _facts(revision)
    sha256 = hashlib.sha256(
        json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return CharacterView(
        character_id=revision.character_id,
        revision_id=revision.id,
        revision_no=revision.revision_no,
        supersedes_id=revision.supersedes_id,
        canonical_name=str(facts["canonical_name"]),
        aliases=facts["aliases"],
        entity_type=str(facts["entity_type"]),
        role=facts["role"],
        gender=facts["gender"],
        status=str(facts["status"]),
        evidence_source_revision_id=facts["evidence_source_revision_id"],
        evidence_segment_ids=facts["evidence_segment_ids"],
        sha256=sha256,
    )


def _relationship_view(row: CharacterRelationship) -> RelationshipView:
    addressing = dict(row.addressing_json or {})
    return RelationshipView(
        id=row.id,
        from_character_id=row.from_character_id,
        to_character_id=row.to_character_id,
        from_ordinal=row.from_ordinal,
        to_ordinal=row.to_ordinal,
        addressing=addressing,
        evidence_source_revision_id=row.evidence_source_revision_id,
        status=row.status,
    )


def _normalize_text(value: str) -> str:
    return value.strip().casefold()


def _looks_like_string_map(value: dict[str, str] | None) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    return all(isinstance(key, str) and isinstance(item, str) for key, item in value.items())
