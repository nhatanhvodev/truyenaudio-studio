from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from app.contracts import JobKind, JobStatus, Usage
from app.db.base import session_factory
from app.db.models import Chapter, Job
from app.modules.budgets.guard import BudgetGuard
from app.modules.compliance.cloud import CloudCallGuard
from app.modules.jobs.runner import JobRunner


MAX_BATCH_CHAPTERS = 50
CLOUD_CAPABLE_STAGES = frozenset({JobKind.TRANSLATE, JobKind.REVIEW, JobKind.SYNTHESIZE})
PauseRequested = Callable[[str, int], bool]


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
    paused: bool = False


@dataclass(frozen=True)
class BatchCloudAuthorization:
    provider_profile_id: str
    cloud_consent_id: str
    budget_authorization_id: str
    estimated_usage: tuple[Usage, ...]
    category: str = "REGULAR"


class BatchBlocked(ValueError):
    pass


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
        *,
        cloud_authorization: BatchCloudAuthorization | None = None,
        cloud_guard: CloudCallGuard | None = None,
        pause_requested: PauseRequested | None = None,
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
            self._validate_cloud_authorization(
                session,
                project_id,
                stage,
                quote_id,
                cloud_authorization,
                cloud_guard,
            )

            job_ids: list[str] = []
            paused = False
            for chapter in ordered:
                if pause_requested is not None and pause_requested(batch_input, len(job_ids)):
                    paused = True
                    break
                job = self.job_runner.enqueue(
                    stage,
                    project_id,
                    chapter.id,
                    _child_idempotency_key(batch_input, chapter.id, stage, chapter.active_source_revision_id or ""),
                    plan=_child_plan(
                        stage,
                        project_id,
                        quote_id,
                        chapter.active_source_revision_id or "",
                        cloud_authorization,
                    ),
                )
                job_ids.append(job.id)
            return self._view(project_id, stage, batch_input, tuple(job_ids), paused=paused)

    def _validate_cloud_authorization(
        self,
        session: Session,
        project_id: str,
        stage: JobKind,
        quote_id: str | None,
        cloud_authorization: BatchCloudAuthorization | None,
        cloud_guard: CloudCallGuard | None,
    ) -> None:
        if stage not in CLOUD_CAPABLE_STAGES:
            return
        if cloud_authorization is None:
            raise BatchBlocked("BATCH_CLOUD_AUTHORIZATION_REQUIRED")
        if quote_id is None or not quote_id.strip():
            raise BatchBlocked("BATCH_QUOTE_REQUIRED")
        if not cloud_authorization.estimated_usage:
            raise BatchBlocked("BATCH_ESTIMATE_REQUIRED")
        guard = cloud_guard or CloudCallGuard(session, BudgetGuard(session))
        decision = guard.evaluate(
            project_id=project_id,
            provider_profile_id=cloud_authorization.provider_profile_id,
            operation_id=quote_id,
            estimated_usage=cloud_authorization.estimated_usage,
            category=cloud_authorization.category,
            stage=stage.value,
            cloud_consent_id=cloud_authorization.cloud_consent_id,
            budget_authorization_id=cloud_authorization.budget_authorization_id,
        )
        if not decision.allowed:
            raise BatchBlocked(",".join(decision.reasons))

    def _view(
        self,
        project_id: str,
        stage: JobKind,
        batch_input: str,
        job_ids: tuple[str, ...],
        *,
        paused: bool,
    ) -> BatchView:
        with self._session_factory() as session:
            rows = session.scalars(select(Job).where(Job.id.in_(job_ids))).all() if job_ids else []
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
            paused=paused,
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


def _child_plan(
    stage: JobKind,
    project_id: str,
    quote_id: str | None,
    active_revision_id: str,
    cloud_authorization: BatchCloudAuthorization | None,
) -> dict[str, object] | None:
    """Immutable plan carried on each cloud-stage child job (J01)."""
    if stage not in CLOUD_CAPABLE_STAGES or cloud_authorization is None:
        return None
    return {
        "stage": stage.value,
        "projectId": project_id,
        "quoteId": quote_id,
        "revisionId": active_revision_id,
        "profileId": cloud_authorization.provider_profile_id,
        "cloudConsentId": cloud_authorization.cloud_consent_id,
        "budgetAuthorizationId": cloud_authorization.budget_authorization_id,
        "category": cloud_authorization.category,
    }
