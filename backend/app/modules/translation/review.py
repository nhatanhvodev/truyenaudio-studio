from __future__ import annotations

from dataclasses import dataclass
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import JobKind, QaSeverity, QaStatus, RunStatus, new_id
from app.db.models import Chapter, QaIssue, SourceSegment, TranslationRun, TranslationSegment
from app.modules.jobs.runner import JobRunner


@dataclass(frozen=True)
class ReviewJob:
    job_id: str
    source_segment_id: str
    kind: JobKind
    idempotency_key: str


class ReviewService:
    def __init__(
        self,
        session: Session,
        *,
        job_runner: object | None = None,
        id_factory=new_id,
    ) -> None:
        self.session = session
        self.job_runner = job_runner or JobRunner(session.get_bind())
        self.id_factory = id_factory

    def enqueue(self, chapter_id: str, *, selected_ids: tuple[str, ...] = ()) -> tuple[ReviewJob, ...]:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError("CHAPTER_NOT_FOUND")
        run = _current_review_run(self.session, chapter_id)
        segment_ids = self._candidate_segment_ids(run.id, selected_ids)

        jobs: list[ReviewJob] = []
        for source_segment_id in segment_ids:
            idempotency_key = _review_key(run.id, source_segment_id)
            job = self.job_runner.enqueue(
                JobKind.REVIEW,
                chapter.project_id,
                chapter.id,
                idempotency_key,
            )
            jobs.append(
                ReviewJob(
                    job_id=job.id,
                    source_segment_id=source_segment_id,
                    kind=JobKind.REVIEW,
                    idempotency_key=idempotency_key,
                )
            )
        return tuple(jobs)

    def _candidate_segment_ids(self, run_id: str, selected_ids: tuple[str, ...]) -> tuple[str, ...]:
        run_segment_ids = self._run_segment_ids(run_id)
        selected = set(selected_ids)
        unknown = selected - set(run_segment_ids)
        if unknown:
            raise ValueError("SOURCE_SEGMENT_NOT_IN_RUN")

        risky = set(
            self.session.scalars(
                select(QaIssue.source_segment_id).where(
                    QaIssue.translation_run_id == run_id,
                    QaIssue.status == QaStatus.OPEN.value,
                    QaIssue.source_segment_id.is_not(None),
                    QaIssue.severity.in_((QaSeverity.MAJOR.value, QaSeverity.CRITICAL.value)),
                )
            ).all()
        )
        candidates = risky | selected
        return tuple(source_segment_id for source_segment_id in run_segment_ids if source_segment_id in candidates)

    def _run_segment_ids(self, run_id: str) -> tuple[str, ...]:
        return tuple(
            self.session.scalars(
                select(TranslationSegment.source_segment_id)
                .where(TranslationSegment.translation_run_id == run_id)
                .join(SourceSegment, SourceSegment.id == TranslationSegment.source_segment_id)
                .order_by(SourceSegment.segment_index, TranslationSegment.id)
            ).all()
        )


def _current_review_run(session: Session, chapter_id: str) -> TranslationRun:
    run = session.scalar(
        select(TranslationRun)
        .where(TranslationRun.chapter_id == chapter_id, TranslationRun.status == RunStatus.REVIEW.value)
        .order_by(TranslationRun.created_at.desc(), TranslationRun.id.desc())
    )
    if run is None:
        raise ValueError("TRANSLATION_RUN_NOT_FOUND")
    return run


def _review_key(run_id: str, source_segment_id: str) -> str:
    return hashlib.sha256(f"review:{run_id}:{source_segment_id}".encode("utf-8")).hexdigest()
