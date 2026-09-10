"""Persisted draft access for jobs (task J04 round 2).

Phase 1 providers are request/response, so the draft stream is rebuilt from
the stored translation segments of the chapter's current run: each segment
becomes one ``segmentReady`` frame in ordinal order. Reconnecting therefore
never re-runs the provider — the client gets a snapshot (offset + text) and
can continue from a bounded, deduplicated stream.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import RunStatus
from app.db.models import Job, SourceSegment, TranslationRun, TranslationSegment
from app.modules.translation.draft_stream import DraftStream


def load_draft_stream(session: Session, job_id: str) -> DraftStream:
    """Rebuild the draft stream for a job from its stored segments."""
    job = session.get(Job, job_id)
    if job is None:
        raise ValueError("JOB_NOT_FOUND")
    run = session.scalar(
        select(TranslationRun)
        .where(TranslationRun.chapter_id == job.chapter_id)
        .order_by(TranslationRun.created_at.desc(), TranslationRun.id.desc())
        .limit(1)
    )
    stream = DraftStream()
    if run is None:
        return stream
    rows = session.execute(
        select(TranslationSegment.target_text)
        .join(SourceSegment, SourceSegment.id == TranslationSegment.source_segment_id)
        .where(TranslationSegment.translation_run_id == run.id)
        .order_by(SourceSegment.segment_index, TranslationSegment.id)
    ).all()
    for (target_text,) in rows:
        stream.apply_segment(1, target_text or "")
    if run.status != RunStatus.RUNNING.value:
        stream.finish()
    return stream


def draft_snapshot(session: Session, job_id: str, after_offset: int | None = None) -> dict[str, object]:
    stream = load_draft_stream(session, job_id)
    snapshot = stream.snapshot(after_offset)
    snapshot["status"] = stream.status
    snapshot["truncated"] = stream.truncated
    snapshot["approvable"] = stream.is_approvable
    return snapshot


def draft_frames(session: Session, job_id: str, *, max_frames: int = 1_000) -> tuple[dict[str, object], ...]:
    """Stored draft frames as (offset_end, text) with a bounded frame count.

    Used by the SSE feed so a reconnecting client can resume by offset id
    without the provider being re-run.
    """
    job = session.get(Job, job_id)
    if job is None:
        raise ValueError("JOB_NOT_FOUND")
    run = session.scalar(
        select(TranslationRun)
        .where(TranslationRun.chapter_id == job.chapter_id)
        .order_by(TranslationRun.created_at.desc(), TranslationRun.id.desc())
        .limit(1)
    )
    if run is None:
        return ()
    rows = session.execute(
        select(TranslationSegment.target_text)
        .join(SourceSegment, SourceSegment.id == TranslationSegment.source_segment_id)
        .where(TranslationSegment.translation_run_id == run.id)
        .order_by(SourceSegment.segment_index, TranslationSegment.id)
    ).all()
    frames: list[dict[str, object]] = []
    offset = 0
    for (target_text,) in rows:
        text = target_text or ""
        offset += len(text)
        frames.append({"offset": offset, "text": text})
    return tuple(frames[-max_frames:])
