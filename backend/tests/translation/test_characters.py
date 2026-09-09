from __future__ import annotations

import hashlib

import pytest

from app.contracts import ChapterState, ImportKind, RightsStatus, SourceType
from app.db.models import (
    Chapter,
    Project,
    SourceRevision,
    SourceSegment,
)
from app.modules.translation.characters import (
    CharacterAliasAmbiguous,
    CharacterService,
    RelationshipCommand,
)


def test_create_candidate_keeps_missing_gender_null(db_session) -> None:
    fixture = _project(db_session)
    service = CharacterService(db_session, id_factory=_ids())

    view = service.create_or_revise(
        fixture["project_id"],
        canonical_name="林动",
        entity_type="PERSON",
        aliases=("Lâm Động", "Lam Dong"),
    )

    assert view.status == "CANDIDATE"
    assert view.revision_no == 1
    assert view.gender is None
    assert view.role is None
    assert view.evidence_source_revision_id is None
    assert view.aliases == ("Lâm Động", "Lam Dong")


def test_gender_validation_rejects_unknown_and_accepts_known(db_session) -> None:
    fixture = _project(db_session)
    service = CharacterService(db_session, id_factory=_ids())

    with pytest.raises(ValueError, match="CHARACTER_GENDER_INVALID"):
        service.create_or_revise(
            fixture["project_id"], canonical_name="X", entity_type="PERSON", gender="ALIEN"
        )

    view = service.create_or_revise(
        fixture["project_id"], canonical_name="Y", entity_type="PERSON", gender="MALE"
    )
    assert view.gender == "MALE"


def test_approve_requires_evidence(db_session) -> None:
    fixture = _project(db_session)
    service = CharacterService(db_session, id_factory=_ids())
    created = service.create_or_revise(
        fixture["project_id"], canonical_name="林动", entity_type="PERSON"
    )

    with pytest.raises(ValueError, match="CHARACTER_EVIDENCE_REQUIRED"):
        service.approve(
            fixture["project_id"],
            created.character_id,
            evidence_source_revision_id=None,
            evidence_segment_ids=(),
        )
    with pytest.raises(ValueError, match="CHARACTER_EVIDENCE_SOURCE_NOT_FOUND"):
        service.approve(
            fixture["project_id"],
            created.character_id,
            evidence_source_revision_id="018f0000-0000-7000-8000-000000000000",
            evidence_segment_ids=("s1",),
        )
    with pytest.raises(ValueError, match="CHARACTER_EVIDENCE_SEGMENTS_INVALID"):
        service.approve(
            fixture["project_id"],
            created.character_id,
            evidence_source_revision_id=fixture["revision_id"],
            evidence_segment_ids=("not-a-segment",),
        )


def test_approve_with_valid_evidence_creates_approved_revision(db_session) -> None:
    fixture = _project(db_session)
    service = CharacterService(db_session, id_factory=_ids())
    created = service.create_or_revise(
        fixture["project_id"], canonical_name="林动", entity_type="PERSON", role="protagonist"
    )

    approved = service.approve(
        fixture["project_id"],
        created.character_id,
        evidence_source_revision_id=fixture["revision_id"],
        evidence_segment_ids=(fixture["segment_id"],),
    )

    assert approved.status == "APPROVED"
    assert approved.revision_no == 2
    assert approved.supersedes_id == created.revision_id
    assert approved.evidence_source_revision_id == fixture["revision_id"]
    assert approved.gender is None


def test_alias_ambiguity_raises_and_unique_alias_resolves(db_session) -> None:
    fixture = _project(db_session)
    service = CharacterService(db_session, id_factory=_ids())
    first = service.create_or_revise(
        fixture["project_id"], canonical_name="Lâm Động", entity_type="PERSON", aliases=("林动",)
    )
    second = service.create_or_revise(
        fixture["project_id"], canonical_name="Lâm Phong", entity_type="PERSON", aliases=("林动",)
    )
    service.approve(
        fixture["project_id"],
        first.character_id,
        evidence_source_revision_id=fixture["revision_id"],
        evidence_segment_ids=(fixture["segment_id"],),
    )
    service.approve(
        fixture["project_id"],
        second.character_id,
        evidence_source_revision_id=fixture["revision_id"],
        evidence_segment_ids=(fixture["segment_id"],),
    )

    with pytest.raises(CharacterAliasAmbiguous):
        service.resolve_alias(fixture["project_id"], "林动")

    resolved = service.resolve_alias(fixture["project_id"], "Lâm Động")
    assert resolved.character_id == first.character_id

    with pytest.raises(ValueError, match="CHARACTER_NOT_FOUND"):
        service.resolve_alias(fixture["project_id"], "Không Có")


def test_relationship_validation_and_chapter_scope(db_session) -> None:
    fixture = _project(db_session)
    service = CharacterService(db_session, id_factory=_ids())
    speaker = service.create_or_revise(fixture["project_id"], canonical_name="A", entity_type="PERSON")
    addressee = service.create_or_revise(fixture["project_id"], canonical_name="B", entity_type="PERSON")
    candidate = service.create_or_revise(fixture["project_id"], canonical_name="C", entity_type="PERSON")
    for character_id in (speaker.character_id, addressee.character_id):
        service.approve(
            fixture["project_id"],
            character_id,
            evidence_source_revision_id=fixture["revision_id"],
            evidence_segment_ids=(fixture["segment_id"],),
        )

    with pytest.raises(ValueError, match="CHARACTER_NOT_APPROVED"):
        service.add_relationship(
            fixture["project_id"],
            RelationshipCommand(
                from_character_id=speaker.character_id,
                to_character_id=candidate.character_id,
                from_ordinal=1,
                to_ordinal=None,
            ),
        )
    with pytest.raises(ValueError, match="CHARACTER_RELATIONSHIP_SELF"):
        service.add_relationship(
            fixture["project_id"],
            RelationshipCommand(
                from_character_id=speaker.character_id,
                to_character_id=speaker.character_id,
                from_ordinal=1,
                to_ordinal=None,
            ),
        )
    with pytest.raises(ValueError, match="CHARACTER_RELATIONSHIP_ORDINAL_INVALID"):
        service.add_relationship(
            fixture["project_id"],
            RelationshipCommand(
                from_character_id=speaker.character_id,
                to_character_id=addressee.character_id,
                from_ordinal=8,
                to_ordinal=3,
            ),
        )

    service.add_relationship(
        fixture["project_id"],
        RelationshipCommand(
            from_character_id=speaker.character_id,
            to_character_id=addressee.character_id,
            from_ordinal=2,
            to_ordinal=4,
            addressing={"call_self": "ta", "call_other": "A công tử"},
        ),
    )

    assert service.active_relationships(fixture["project_id"], 1) == ()
    assert len(service.active_relationships(fixture["project_id"], 2)) == 1
    assert len(service.active_relationships(fixture["project_id"], 4)) == 1
    assert service.active_relationships(fixture["project_id"], 5) == ()


def test_relationship_rejects_cross_project_character(db_session) -> None:
    project_a = _project(db_session)
    project_b = _project(db_session, slug="other-project")
    service = CharacterService(db_session, id_factory=_ids())
    in_a = service.create_or_revise(project_a["project_id"], canonical_name="A", entity_type="PERSON")
    in_b = service.create_or_revise(project_b["project_id"], canonical_name="B", entity_type="PERSON")
    service.approve(
        project_a["project_id"],
        in_a.character_id,
        evidence_source_revision_id=project_a["revision_id"],
        evidence_segment_ids=(project_a["segment_id"],),
    )
    service.approve(
        project_b["project_id"],
        in_b.character_id,
        evidence_source_revision_id=project_b["revision_id"],
        evidence_segment_ids=(project_b["segment_id"],),
    )

    with pytest.raises(ValueError, match="CHARACTER_NOT_FOUND|CHARACTER_RELATIONSHIP_CROSS_PROJECT"):
        service.add_relationship(
            project_a["project_id"],
            RelationshipCommand(
                from_character_id=in_a.character_id,
                to_character_id=in_b.character_id,
                from_ordinal=1,
                to_ordinal=None,
            ),
        )


_ID_SEQ = [0]


def _next_id() -> str:
    _ID_SEQ[0] += 1
    return f"018f0000-0000-7000-8000-{_ID_SEQ[0]:012x}"


def _project(db_session, *, slug: str = "chars") -> dict[str, str]:
    project = Project(
        id=_next_id(),
        title="Truyen",
        slug=slug,
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
        default_language="zh-CN",
        target_language="vi-VN",
    )
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(
        id=_next_id(),
        project_id=project.id,
        ordinal=1,
        state=ChapterState.NORMALIZED.value,
    )
    db_session.add(chapter)
    db_session.flush()
    revision = SourceRevision(
        id=_next_id(),
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text="林动抬头。",
        normalized_sha256=hashlib.sha256("林动抬头。".encode("utf-8")).hexdigest(),
        han_char_count=0,
        total_char_count=5,
        normalizer_version="nfc-v1",
    )
    db_session.add(revision)
    db_session.flush()
    chapter.active_source_revision_id = revision.id
    segment = SourceSegment(
        id=_next_id(),
        source_revision_id=revision.id,
        segment_index=0,
        paragraph_start=0,
        paragraph_end=0,
        source_text="林动抬头。",
        source_sha256=hashlib.sha256("林动抬头。".encode("utf-8")).hexdigest(),
        segment_kind="SOURCE",
    )
    db_session.add(segment)
    db_session.flush()
    return {
        "project_id": project.id,
        "chapter_id": chapter.id,
        "revision_id": revision.id,
        "segment_id": segment.id,
    }


def _ids():
    def factory() -> str:
        return _next_id()

    return factory
