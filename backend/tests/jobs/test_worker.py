from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from app.contracts import JobKind, JobStatus, RightsStatus, SourceType
from app.modules.jobs.runner import JobLease, JobRunner
from app.worker import Worker, build_default_handlers


NOW = datetime(2026, 8, 19, 3, 0, 0, tzinfo=UTC)
PROJECT_ID = "018f0000-0000-7000-8000-000000001101"
CHAPTER_ID = "018f0000-0000-7000-8000-000000001102"


@pytest.fixture
def runner(migrated_engine: Engine, deterministic_uuid7_factory) -> JobRunner:
    _insert_project(migrated_engine)
    return JobRunner(migrated_engine, id_factory=deterministic_uuid7_factory)


class ManualClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        current = self.value
        self.value = current + timedelta(seconds=1)
        return current


def _insert_project(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects (id, title, slug, source_type, rights_status, created_at, updated_at)
                VALUES (:id, 'Worker Project', 'worker-project', :source_type, :rights_status, :now, :now)
                """
            ),
            {
                "id": PROJECT_ID,
                "source_type": SourceType.SELF_AUTHORED.value,
                "rights_status": RightsStatus.PRIVATE_ONLY.value,
                "now": NOW.isoformat(),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO chapters (id, project_id, ordinal, state, created_at, updated_at)
                VALUES (:id, :project_id, 1, 'IMPORTED', :now, :now)
                """
            ),
            {"id": CHAPTER_ID, "project_id": PROJECT_ID, "now": NOW.isoformat()},
        )


def _open_attempt_count(engine: Engine, job_id: str) -> int:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT COUNT(*) FROM job_attempts WHERE job_id = :job_id AND finished_at IS NULL"),
            {"job_id": job_id},
        ).scalar_one()


def _attempt_outcome(engine: Engine, job_id: str) -> str | None:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT outcome FROM job_attempts WHERE job_id = :job_id ORDER BY attempt_no DESC LIMIT 1"),
            {"job_id": job_id},
        ).scalar_one()


@pytest.mark.asyncio
async def test_run_once_returns_false_when_no_job_is_available(runner: JobRunner) -> None:
    worker = Worker(runner, handlers={}, worker_id="worker-a", clock=ManualClock(NOW))

    assert await worker.run_once() is False


def test_default_handler_registry_uses_recovery_context_for_production_stage_paths() -> None:
    handlers = build_default_handlers()

    assert {JobKind.TRANSLATE, JobKind.SYNTHESIZE, JobKind.MASTER, JobKind.EXPORT} <= set(handlers)
    for stage in (JobKind.TRANSLATE, JobKind.SYNTHESIZE, JobKind.MASTER, JobKind.EXPORT):
        assert tuple(handlers[stage].__code__.co_varnames[:2]) == ("lease", "recovery")


@pytest.mark.asyncio
async def test_run_once_dispatches_one_job_and_completes_none_result_without_artifact(runner: JobRunner) -> None:
    job = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "translate")
    seen: list[JobLease] = []

    async def handle(lease: JobLease) -> None:
        seen.append(lease)
        return None

    worker = Worker(runner, handlers={JobKind.TRANSLATE: handle}, worker_id="worker-a", clock=ManualClock(NOW))

    assert await worker.run_once() is True
    assert [lease.job_id for lease in seen] == [job.id]
    view = runner.get(job.id)
    assert view.status is JobStatus.SUCCEEDED
    assert view.result_artifact_id is None
    assert view.lease_owner is None
    assert _open_attempt_count(runner.engine, job.id) == 0


@pytest.mark.asyncio
async def test_missing_handler_fails_valid_lease_without_attempt_leak(runner: JobRunner) -> None:
    job = runner.enqueue(JobKind.REVIEW, PROJECT_ID, CHAPTER_ID, "unsupported")
    worker = Worker(runner, handlers={JobKind.TRANSLATE: _unused_handler}, worker_id="worker-a", clock=ManualClock(NOW))

    assert await worker.run_once() is True
    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.error_code == "INPUT_UNSUPPORTED_JOB_KIND"
    assert "REVIEW" in (view.error_summary or "")
    assert view.lease_owner is None
    assert _open_attempt_count(runner.engine, job.id) == 0


@pytest.mark.asyncio
async def test_cancel_requested_immediately_after_claim_is_acknowledged_before_dispatch(
    runner: JobRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "cancel-before-dispatch")
    original_claim = runner.claim

    def claim_then_cancel(worker_id: str, now: datetime) -> JobLease | None:
        lease = original_claim(worker_id, now)
        if lease is not None:
            runner.request_cancel(lease.job_id, NOW + timedelta(seconds=5))
        return lease

    monkeypatch.setattr(runner, "claim", claim_then_cancel)
    worker = Worker(runner, handlers={JobKind.TRANSLATE: _unused_handler}, worker_id="worker-a", clock=ManualClock(NOW))

    assert await worker.run_once() is True
    view = runner.get(job.id)
    assert view.status is JobStatus.CANCELED
    assert view.lease_owner is None
    assert _attempt_outcome(runner.engine, job.id) == "CANCELED"
    assert _open_attempt_count(runner.engine, job.id) == 0


@pytest.mark.asyncio
async def test_cancel_requested_during_long_handler_keeps_lease_heartbeating_until_acknowledged(
    runner: JobRunner,
) -> None:
    job = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "cancel-long-handler")
    clock = ManualClock(NOW)
    original_lease_expires_at: datetime | None = None

    async def handle(lease: JobLease) -> None:
        nonlocal original_lease_expires_at
        original_lease_expires_at = lease.lease_expires_at
        runner.request_cancel(lease.job_id, NOW + timedelta(seconds=5))
        clock.value = NOW + timedelta(seconds=30)
        while runner.get(lease.job_id).lease_expires_at == original_lease_expires_at:
            await asyncio.sleep(0)
        clock.value = NOW + timedelta(seconds=61)
        return None

    worker = Worker(
        runner,
        handlers={JobKind.TRANSLATE: handle},
        worker_id="worker-a",
        heartbeat_interval_seconds=0,
        clock=clock,
    )

    assert await asyncio.wait_for(worker.run_once(), timeout=1) is True
    assert original_lease_expires_at == NOW + timedelta(seconds=60)
    view = runner.get(job.id)
    assert view.status is JobStatus.CANCELED
    assert view.lease_owner is None
    assert view.lease_expires_at is None
    assert _attempt_outcome(runner.engine, job.id) == "CANCELED"
    assert _open_attempt_count(runner.engine, job.id) == 0


@pytest.mark.asyncio
async def test_handler_exception_is_redacted_and_closes_attempt(runner: JobRunner) -> None:
    job = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "exception")

    async def handle(lease: JobLease) -> str:
        raise RuntimeError("secret-token=abc123 path=D:/private/source.py")

    worker = Worker(runner, handlers={JobKind.TRANSLATE: handle}, worker_id="worker-a", clock=ManualClock(NOW))

    assert await worker.run_once() is True
    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.error_code == "WORKER_HANDLER_EXCEPTION"
    assert view.error_summary == "RuntimeError while processing TRANSLATE"
    with runner.engine.connect() as connection:
        detail = connection.execute(
            text("SELECT redacted_detail FROM job_attempts WHERE job_id = :job_id"),
            {"job_id": job.id},
        ).scalar_one()
    assert detail == "RuntimeError"
    assert "secret-token" not in (view.error_summary or "")
    assert "source.py" not in (detail or "")
    assert _open_attempt_count(runner.engine, job.id) == 0


@pytest.mark.asyncio
async def test_running_job_cancel_is_acknowledged_after_handler_without_complete(runner: JobRunner) -> None:
    job = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "cancel-after")

    async def handle(lease: JobLease) -> str:
        runner.request_cancel(lease.job_id, NOW + timedelta(seconds=5))
        return "018f0000-0000-7000-8000-000000009999"

    worker = Worker(runner, handlers={JobKind.TRANSLATE: handle}, worker_id="worker-a", clock=ManualClock(NOW))

    assert await worker.run_once() is True
    view = runner.get(job.id)
    assert view.status is JobStatus.CANCELED
    assert view.result_artifact_id is None
    assert view.lease_owner is None
    assert _attempt_outcome(runner.engine, job.id) == "CANCELED"
    assert _open_attempt_count(runner.engine, job.id) == 0


@pytest.mark.asyncio
async def test_heartbeat_runs_during_slow_handler_and_stops_after_completion(runner: JobRunner) -> None:
    job = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "heartbeat")
    heartbeat_seen = asyncio.Event()

    async def handle(lease: JobLease) -> None:
        while runner.get(lease.job_id).lease_expires_at == lease.lease_expires_at:
            await asyncio.sleep(0)
        heartbeat_seen.set()
        return None

    worker = Worker(
        runner,
        handlers={JobKind.TRANSLATE: handle},
        worker_id="worker-a",
        heartbeat_interval_seconds=0,
        clock=ManualClock(NOW),
    )

    assert await asyncio.wait_for(worker.run_once(), timeout=1) is True
    assert heartbeat_seen.is_set()
    completed = runner.get(job.id)
    assert completed.status is JobStatus.SUCCEEDED
    assert completed.lease_expires_at is None
    await asyncio.sleep(0)
    assert _open_attempt_count(runner.engine, job.id) == 0


async def _unused_handler(lease: JobLease) -> str | None:
    raise AssertionError("handler should not run")
