from __future__ import annotations

import pytest

from app.contracts import ChapterState, RightsStatus, SourceType
from app.db.models import Chapter
from app.modules.projects.state_machine import InvalidChapterTransition, next_state
from app.modules.projects.workflow import CreateProject, ImportChapters, ProjectWorkflow


@pytest.fixture
def workflow(db_session, artifact_store) -> ProjectWorkflow:
    return ProjectWorkflow(db_session, artifact_store)


@pytest.fixture
def project(workflow: ProjectWorkflow):
    return workflow.create_project(
        CreateProject(
            "State",
            "state",
            SourceType.USER_SUPPLIED_PRIVATE,
            RightsStatus.PRIVATE_ONLY,
            None,
            None,
        )
    )


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        (ChapterState.NORMALIZED, ChapterState.TRANSLATING),
        (ChapterState.TRANSLATING, ChapterState.TRANSLATION_REVIEW),
        (ChapterState.TRANSLATION_REVIEW, ChapterState.TRANSLATION_APPROVED),
        (ChapterState.TRANSLATION_APPROVED, ChapterState.VOICE_CONFIGURED),
        (ChapterState.VOICE_CONFIGURED, ChapterState.TTS_QUEUED),
        (ChapterState.TTS_QUEUED, ChapterState.SYNTHESIZING),
        (ChapterState.SYNTHESIZING, ChapterState.AUDIO_REVIEW),
        (ChapterState.AUDIO_REVIEW, ChapterState.READY_TO_EXPORT),
        (ChapterState.READY_TO_EXPORT, ChapterState.EXPORTED),
        (ChapterState.TRANSLATING, ChapterState.FAILED),
    ],
)
def test_state_machine_allows_declared_forward_transitions(current: ChapterState, requested: ChapterState) -> None:
    assert next_state(current, requested) is requested


def test_state_machine_rejects_skipping_review_gates() -> None:
    with pytest.raises(InvalidChapterTransition):
        next_state(ChapterState.NORMALIZED, ChapterState.TRANSLATION_APPROVED)

    with pytest.raises(InvalidChapterTransition):
        next_state(ChapterState.TRANSLATION_APPROVED, ChapterState.READY_TO_EXPORT)


def test_workflow_transition_uses_state_machine(workflow: ProjectWorkflow, project, db_session) -> None:
    (chapter,) = workflow.import_chapters(project.id, ImportChapters.paste(1, "一", "甲。"))

    with pytest.raises(InvalidChapterTransition):
        workflow.request_transition(chapter.id, ChapterState.READY_TO_EXPORT)

    transitioned = workflow.request_transition(chapter.id, ChapterState.TRANSLATING)
    assert transitioned.state is ChapterState.TRANSLATING
    stored = db_session.get(Chapter, chapter.id)
    assert stored is not None
    assert stored.state == ChapterState.TRANSLATING.name
