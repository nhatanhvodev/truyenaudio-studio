from __future__ import annotations

from app.contracts import RightsStatus, SourceType
from app.db.models import Project, StoryMemoryEntry
from app.modules.translation.story_memory import StoryMemoryService


def test_memory_is_limited_to_current_ordinal(db_session) -> None:
    project = Project(
        id="018f0000-0000-7000-8000-100000000101",
        title="Truyen",
        slug="story-memory",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
    )
    other_project = Project(
        id="018f0000-0000-7000-8000-100000000102",
        title="Khac",
        slug="story-memory-other",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
    )
    db_session.add_all((project, other_project))
    db_session.add_all(
        (
            StoryMemoryEntry(
                id="018f0000-0000-7000-8000-200000000101",
                project_id=project.id,
                entity_key="lin-dong",
                entity_type="character",
                summary="Lin Dong is still hiding his identity.",
                valid_from_ordinal=1,
                valid_to_ordinal=5,
                revision_no=1,
            ),
            StoryMemoryEntry(
                id="018f0000-0000-7000-8000-200000000102",
                project_id=project.id,
                entity_key="future-sect",
                entity_type="faction",
                summary="This sect appears later.",
                valid_from_ordinal=6,
                valid_to_ordinal=None,
                revision_no=1,
            ),
            StoryMemoryEntry(
                id="018f0000-0000-7000-8000-200000000103",
                project_id=project.id,
                entity_key="expired-city",
                entity_type="place",
                summary="This city arc is finished.",
                valid_from_ordinal=1,
                valid_to_ordinal=4,
                revision_no=1,
            ),
            StoryMemoryEntry(
                id="018f0000-0000-7000-8000-200000000104",
                project_id=other_project.id,
                entity_key="other-project",
                entity_type="character",
                summary="Must not leak across projects.",
                valid_from_ordinal=1,
                valid_to_ordinal=None,
                revision_no=1,
            ),
        )
    )
    db_session.commit()

    service = StoryMemoryService(db_session)

    assert [entry.entity_key for entry in service.memory_for(project.id, 5)] == ["lin-dong"]
