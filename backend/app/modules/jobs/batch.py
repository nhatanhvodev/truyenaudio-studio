from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json

from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from app.contracts import JobKind, JobStatus
from app.db.base import session_factory
from app.db.models import Chapter, Job
from app.modules.jobs.runner import JobRunner


MAX_BATCH_CHAPTERS = 50


@dataclass(frozen=True)
class BatchView:
    batch_id: str
    project_id: str
    stage: JobKind
    total: int
    queued: int
    running: int
    succeeded: int
    failed: int
    canceled: int
    blocked: int
    job_ids: tuple[str, ...]


class BatchCoordinator:
    def __init__(
        self,
        engine: Engine,
        *,
        job_runner: JobRunner | None = None,
        session_factory_: sessionmaker | None = None,
    ) -> None:
        self.engine = engine
        self.job_runner = job_runner or JobRunner(engine)
        self._session_factory = session_factory_ or session_factory(engine)

    def enqueue_batch(
        self,
        project_id: str,
        chapter_ids: tuple[str, ...],
        stage: JobKind,
        quote_id: str | None,
    ) -> BatchView:
        if not 1 <= len(chapter_ids) <= MAX_BATCH_CHAPTERS:
            raise ValueError("BATCH_CHAPTER_LIMIT_EXCEEDED")
        if len(set(chapter_ids)) != len(chapter_ids):
            raise ValueError("BATCH_CHAPTER_DUPLICATE")

        with self._session_factory() as session:
            chapters = session.scalars(
                select(Chapter).where(Chapter.project_id == project_id, Chapter.id.in_(chapter_ids))
            ).all()
            by_id = {chapter.id: chapter for chapter in chapters}
            if len(by_id) != len(chapter_ids):
                raise ValueError("BATCH_CHAPTER_NOT_FOUND")
            ordered = tuple(by_id[chapter_id] for chapter_id in chapter_ids)
            if any(chapter.active_source_revision_id is None for chapter in ordered):
                raise ValueError("BATCH_CHAPTER_REVISION_REQUIRED")

            batch_input = _batch_input_hash(project_id, chapter_ids, stage, quote_id)
            job_ids = tuple(
                self.job_runner.enqueue(
                    stage,
                    project_id,
                    chapter.id,
                    _child_idempotency_key(batch_input, chapter.id, stage, chapter.active_source_revision_id or ""),
                ).id
                for chapter in ordered
            )
            return self._view(project_id, stage, batch_input, job_ids)

    def _view(self, project_id: str, stage: JobKind, batch_input: str, job_ids: tuple[str, ...]) -> BatchView:
        with self._session_factory() as session:
            rows = session.scalars(select(Job).where(Job.id.in_(job_ids))).all()
        counts = Counter(JobStatus(row.status) for row in rows)
        return BatchView(
            batch_id=batch_input,
            project_id=project_id,
            stage=stage,
            total=len(job_ids),
            queued=counts[JobStatus.QUEUED],
            running=counts[JobStatus.RUNNING] + counts[JobStatus.CANCEL_REQUESTED],
            succeeded=counts[JobStatus.SUCCEEDED],
            failed=counts[JobStatus.FAILED] + counts[JobStatus.BILLING_UNKNOWN],
            canceled=counts[JobStatus.CANCELED],
            blocked=counts[JobStatus.BLOCKED_BUDGET],
            job_ids=job_ids,
        )


def _batch_input_hash(project_id: str, chapter_ids: tuple[str, ...], stage: JobKind, quote_id: str | None) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "project_id": project_id,
                "chapter_ids": chapter_ids,
                "stage": stage.value,
                "quote_id": quote_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _child_idempotency_key(batch_input: str, chapter_id: str, stage: JobKind, active_revision_id: str) -> str:
    return hashlib.sha256(
        f"{batch_input}:{chapter_id}:{stage.value}:{active_revision_id}".encode("utf-8")
    ).hexdigest()
