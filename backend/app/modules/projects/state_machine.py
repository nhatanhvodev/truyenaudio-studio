from __future__ import annotations

from app.contracts import ChapterState


class InvalidChapterTransition(ValueError):
    """Raised when a requested chapter state change is outside the workflow table."""


ALLOWED_TRANSITIONS: dict[ChapterState, frozenset[ChapterState]] = {
    ChapterState.IMPORTED: frozenset({ChapterState.NORMALIZED, ChapterState.FAILED}),
    ChapterState.NORMALIZED: frozenset({ChapterState.TRANSLATING, ChapterState.FAILED}),
    ChapterState.TRANSLATING: frozenset({ChapterState.TRANSLATION_REVIEW, ChapterState.FAILED}),
    ChapterState.TRANSLATION_REVIEW: frozenset({ChapterState.TRANSLATION_APPROVED, ChapterState.FAILED}),
    ChapterState.TRANSLATION_APPROVED: frozenset({ChapterState.VOICE_CONFIGURED, ChapterState.FAILED}),
    ChapterState.VOICE_CONFIGURED: frozenset({ChapterState.TTS_QUEUED, ChapterState.FAILED}),
    ChapterState.TTS_QUEUED: frozenset({ChapterState.SYNTHESIZING, ChapterState.FAILED}),
    ChapterState.SYNTHESIZING: frozenset({ChapterState.AUDIO_REVIEW, ChapterState.FAILED}),
    ChapterState.AUDIO_REVIEW: frozenset({ChapterState.READY_TO_EXPORT, ChapterState.FAILED}),
    ChapterState.READY_TO_EXPORT: frozenset({ChapterState.EXPORTED, ChapterState.FAILED}),
    ChapterState.EXPORTED: frozenset(),
    ChapterState.FAILED: frozenset(),
}


def next_state(current: ChapterState | str, requested: ChapterState | str) -> ChapterState:
    current_state = ChapterState(current)
    requested_state = ChapterState(requested)
    if requested_state == current_state:
        return requested_state
    if requested_state not in ALLOWED_TRANSITIONS[current_state]:
        raise InvalidChapterTransition(f"cannot transition chapter from {current_state} to {requested_state}")
    return requested_state
