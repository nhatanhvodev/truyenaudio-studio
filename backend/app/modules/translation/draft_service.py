"""Persisted draft access for jobs (task J04 rounds 2 and 4).

Phase 1 providers are request/response, so the draft stream is rebuilt from
storage instead of being re-run: each segment of the chapter's latest run
becomes one ``segmentReady`` frame in ordinal order. Reconnecting therefore
never calls the provider — the client gets a snapshot (offset + text) and can
continue from a bounded, deduplicated stream.

When the worker has persisted streaming deltas (U04 ``workspace_drafts``), the
stored draft text **overlays** the run's approved segment text for the same
base source revision: partial deltas are visible, and approved translation
segments stay untouched. The draft revision is reported so a reconnecting
client can tell which stored revision it is looking at.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import RunStatus
from app.db.models import Job, SourceSegment, TranslationRun, TranslationSegment, WorkspaceDraft
from app.modules.translation.draft_stream import DraftStream


def _latest_run(session: Session, chapter_id: str) -> TranslationRun | None:
    return session.scalar(
        select(TranslationRun)
        .where(TranslationRun.chapter_id == chapter_id)
        .order_by(TranslationRun.created_at.desc(), TranslationRun.id.desc())
        .limit(1)
    )


def _draft_content(
    session: Session, chapter_id: str, base_revision_id: str
) -> tuple[dict[str, str], int | None]:
    row = session.scalar(
        select(WorkspaceDraft).where(
            WorkspaceDraft.chapter_id == chapter_id,
            WorkspaceDraft.base_revision_id == base_revision_id,
        )
    )
    if row is None:
        return {}, None
    return dict(row.content_json or {}), row.revision


def _segment_texts(session: Session, job_id: str) -> tuple[tuple[str, ...], TranslationRun | None, int | None]:
    """Stored per-segment draft text (draft overlaid on the run) in ordinal order."""
    job = session.get(Job, job_id)
    if job is None:
        raise ValueError("JOB_NOT_FOUND")
    run = _latest_run(session, job.chapter_id)
    if run is None:
        return (), None, None
    content, draft_revision = _draft_content(session, job.chapter_id, run.source_revision_id)
    rows = session.execute(
        select(SourceSegment.id, TranslationSegment.target_text)
        .join(TranslationSegment, TranslationSegment.source_segment_id == SourceSegment.id)
        .where(TranslationSegment.translation_run_id == run.id)
        .order_by(SourceSegment.segment_index, TranslationSegment.id)
    ).all()
    texts = tuple(content.get(segment_id, target_text or "") for segment_id, target_text in rows)
    return texts, run, draft_revision


def _build_stream(texts: tuple[str, ...], run: TranslationRun | None) -> DraftStream:
    stream = DraftStream()
    if run is None:
        return stream
    for text in texts:
        stream.apply_segment(1, text)
    if run.status != RunStatus.RUNNING.value:
        stream.finish()
    return stream


def load_draft_stream(session: Session, job_id: str) -> DraftStream:
    """Rebuild the draft stream for a job from stored draft/segment text."""
    texts, run, _ = _segment_texts(session, job_id)
    return _build_stream(texts, run)


def draft_snapshot(session: Session, job_id: str, after_offset: int | None = None) -> dict[str, object]:
    texts, run, draft_revision = _segment_texts(session, job_id)
    stream = _build_stream(texts, run)
    snapshot = stream.snapshot(after_offset)
    snapshot["status"] = stream.status
    snapshot["truncated"] = stream.truncated
    snapshot["approvable"] = stream.is_approvable
    snapshot["draftRevision"] = draft_revision
    return snapshot


def draft_frames(session: Session, job_id: str, *, max_frames: int = 1_000) -> tuple[dict[str, object], ...]:
    """Stored draft frames as (offset_end, text) with a bounded frame count.

    Used by the SSE feed so a reconnecting client can resume by offset id
    without the provider being re-run.
    """
    texts, run, _ = _segment_texts(session, job_id)
    if run is None:
        return ()
    frames: list[dict[str, object]] = []
    offset = 0
    for text in texts:
        offset += len(text)
        frames.append({"offset": offset, "text": text})
    return tuple(frames[-max_frames:])
