from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from app.contracts import ArtifactKind, ArtifactStatus, JobKind, JobStatus, SourceType, RightsStatus
from app.modules.jobs.retry import RetryDecision, RetryDisposition, classify_retry
from app.modules.jobs.runner import (
    HEARTBEAT_INTERVAL_SECONDS,
    LEASE_SECONDS,
    InvalidJobTransition,
    LeaseLost,
    RECOVERY_THRESHOLD_SECONDS,
    ErrorRecord,
    JobRunner,
)


NOW = datetime(2026, 8, 19, 2, 0, 0, tzinfo=UTC)
PROJECT_ID = "018f0000-0000-7000-8000-000000000101"
CHAPTER_ID = "018f0000-0000-7000-8000-000000000102"


@pytest.fixture
def runner(migrated_engine: Engine, deterministic_uuid7_factory) -> JobRunner:
    _insert_project(migrated_engine)
    return JobRunner(migrated_engine, id_factory=deterministic_uuid7_factory)


def _insert_project(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects (id, title, slug, source_type, rights_status, created_at, updated_at)
                VALUES (:id, 'Queue Project', 'queue-project', :source_type, :rights_status, :now, :now)
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


def _attempt_rows(engine: Engine, job_id: str) -> list[tuple[int, str | None, str | None, str | None]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT attempt_no, finished_at, outcome, provider_request_id
                FROM job_attempts
                WHERE job_id = :job_id
                ORDER BY attempt_no
                """
            ),
            {"job_id": job_id},
        ).all()
    return [(row.attempt_no, row.finished_at, row.outcome, row.provider_request_id) for row in rows]


def _open_attempt_count(engine: Engine, job_id: str) -> int:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT COUNT(*) FROM job_attempts WHERE job_id = :job_id AND finished_at IS NULL"),
            {"job_id": job_id},
        ).scalar_one()


def _insert_artifact(engine: Engine, artifact_id: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO artifacts
                (id, chapter_id, kind, status, relative_path, sha256, byte_size, mime_type,
                 input_hash, settings_hash, created_at, updated_at)
                VALUES
                (:id, :chapter_id, :kind, :status, :relative_path, :sha256, 1, 'application/json',
                 :input_hash, :settings_hash, :now, :now)
                """
            ),
            {
                "id": artifact_id,
                "chapter_id": CHAPTER_ID,
                "kind": ArtifactKind.REPORT.value,
                "status": ArtifactStatus.READY.value,
                "relative_path": f"reports/{artifact_id}.json",
                "sha256": "a" * 64,
                "input_hash": f"{int(artifact_id[-12:], 16):064x}",
                "settings_hash": "c" * 64,
                "now": NOW.isoformat(),
            },
        )


def test_enqueue_is_idempotent_and_returns_immutable_view(runner: JobRunner, migrated_engine: Engine) -> None:
    first = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "translate-same", priority=10)
    second = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "translate-same", priority=1)

    assert second == first
    assert first.kind is JobKind.TRANSLATE
    assert first.status is JobStatus.QUEUED
    with pytest.raises(Exception, match="cannot assign to field"):
        first.priority = 99  # type: ignore[misc]

    with migrated_engine.connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM jobs WHERE idempotency_key = 'translate-same'")).scalar_one()
    assert count == 1


def test_claim_uses_stable_priority_then_id_order_and_creates_first_attempt(runner: JobRunner) -> None:
    later_low_priority = runner.enqueue(JobKind.REVIEW, PROJECT_ID, CHAPTER_ID, "priority-20", priority=20)
    first_same_priority = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "priority-5a", priority=5)
    runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "priority-5b", priority=5)

    claim = runner.claim("worker-a", NOW)

    assert claim is not None
    assert claim.job_id == first_same_priority.id
    assert claim.attempt_no == 1
    assert claim.worker_id == "worker-a"
    assert claim.lease_expires_at == NOW + timedelta(seconds=LEASE_SECONDS)
    assert runner.get(first_same_priority.id).status is JobStatus.RUNNING
    assert runner.get(later_low_priority.id).status is JobStatus.QUEUED


def test_two_independent_runners_racing_claim_exactly_one_job(
    migrated_engine: Engine,
    deterministic_uuid7_factory,
) -> None:
    _insert_project(migrated_engine)
    owner = JobRunner(migrated_engine, id_factory=deterministic_uuid7_factory)
    job = owner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "race-once")
    runners = [
        JobRunner(migrated_engine, id_factory=deterministic_uuid7_factory),
        JobRunner(migrated_engine, id_factory=deterministic_uuid7_factory),
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda args: args[0].claim(args[1], NOW), zip(runners, ["worker-a", "worker-b"])))

    claimed = [claim for claim in claims if claim is not None]
    assert len(claimed) == 1
    assert claimed[0].job_id == job.id
    assert _attempt_rows(migrated_engine, job.id) == [(1, None, None, None)]


def test_claim_ignores_future_next_run_and_does_not_reclaim_fresh_lease(runner: JobRunner) -> None:
    delayed = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "delayed")
    ready = runner.enqueue(JobKind.REVIEW, PROJECT_ID, CHAPTER_ID, "ready")
    runner.defer(delayed.id, NOW + timedelta(seconds=2))

    assert runner.claim("worker-a", NOW).job_id == ready.id
    assert runner.claim("worker-c", NOW + timedelta(seconds=1)) is None


def test_heartbeat_only_extends_matching_owner_lease(runner: JobRunner) -> None:
    job = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "heartbeat-owner")
    runner.claim("worker-a", NOW)

    assert runner.heartbeat(job.id, "worker-b", NOW + timedelta(seconds=15)) is None
    still_owned = runner.get(job.id)
    assert still_owned.lease_owner == "worker-a"
    assert still_owned.lease_expires_at == NOW + timedelta(seconds=LEASE_SECONDS)

    updated = runner.heartbeat(job.id, "worker-a", NOW + timedelta(seconds=15))
    assert updated is not None
    assert updated.heartbeat_at == NOW + timedelta(seconds=15)
    assert runner.get(job.id).lease_expires_at == NOW + timedelta(seconds=15 + LEASE_SECONDS)
    assert HEARTBEAT_INTERVAL_SECONDS == 15


def test_heartbeat_does_not_revive_already_expired_lease(runner: JobRunner) -> None:
    job = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "heartbeat-expired")
    runner.claim("worker-a", NOW)

    assert runner.heartbeat(job.id, "worker-a", NOW + timedelta(seconds=LEASE_SECONDS + 1)) is None
    expired = runner.get(job.id)
    assert expired.lease_owner == "worker-a"
    assert expired.lease_expires_at == NOW + timedelta(seconds=LEASE_SECONDS)


def test_cancel_requested_lease_stays_heartbeatable_only_for_matching_owner_and_unexpired_lease(
    runner: JobRunner,
) -> None:
    job = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "heartbeat-cancel")
    lease = runner.claim("worker-a", NOW)
    assert lease is not None
    runner.request_cancel(job.id, NOW + timedelta(seconds=1))

    assert runner.heartbeat(job.id, "worker-b", NOW + timedelta(seconds=15)) is None
    still_owned = runner.get(job.id)
    assert still_owned.status is JobStatus.CANCEL_REQUESTED
    assert still_owned.lease_owner == "worker-a"
    assert still_owned.lease_expires_at == NOW + timedelta(seconds=LEASE_SECONDS)

    updated = runner.heartbeat(job.id, "worker-a", NOW + timedelta(seconds=15))
    assert updated is not None
    assert updated.heartbeat_at == NOW + timedelta(seconds=15)
    assert runner.get(job.id).lease_expires_at == NOW + timedelta(seconds=15 + LEASE_SECONDS)
    assert runner.heartbeat(job.id, "worker-a", NOW + timedelta(seconds=15 + LEASE_SECONDS + 1)) is None


def test_request_cancel_marks_only_nonterminal_eligible_jobs(runner: JobRunner) -> None:
    succeeded = runner.enqueue(JobKind.EXPORT, PROJECT_ID, CHAPTER_ID, "cancel-succeeded", priority=1)
    running = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "cancel-running", priority=5)
    queued = runner.enqueue(JobKind.REVIEW, PROJECT_ID, CHAPTER_ID, "cancel-queued", priority=10)
    artifact_id = "018f0000-0000-7000-8000-000000000777"
    _insert_artifact(runner.engine, artifact_id)
    lease = runner.claim("worker-a", NOW)
    assert lease is not None
    runner.complete(succeeded.id, artifact_id, lease.worker_id, lease.attempt_id, NOW + timedelta(seconds=1))
    assert runner.claim("worker-a", NOW + timedelta(seconds=2)).job_id == running.id

    assert runner.request_cancel(running.id, NOW + timedelta(seconds=3)).status is JobStatus.CANCEL_REQUESTED
    assert runner.request_cancel(queued.id, NOW + timedelta(seconds=3)).status is JobStatus.CANCELED
    assert runner.request_cancel(succeeded.id, NOW + timedelta(seconds=3)) is None
    assert runner.get(succeeded.id).status is JobStatus.SUCCEEDED


def test_request_cancel_terminally_cancels_never_started_queued_job(runner: JobRunner, migrated_engine: Engine) -> None:
    queued = runner.enqueue(JobKind.REVIEW, PROJECT_ID, CHAPTER_ID, "cancel-never-started")

    canceled = runner.request_cancel(queued.id, NOW + timedelta(seconds=1))

    assert canceled is not None
    assert canceled.status is JobStatus.CANCELED
    assert canceled.cancel_requested_at == NOW + timedelta(seconds=1)
    assert canceled.lease_owner is None
    assert canceled.lease_expires_at is None
    assert runner.claim("worker-a", NOW + timedelta(seconds=2)) is None
    assert _attempt_rows(migrated_engine, queued.id) == []


def test_complete_and_fail_terminal_paths_clear_lease_and_close_attempt(runner: JobRunner, migrated_engine: Engine) -> None:
    succeeded = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "complete")
    artifact_id = "018f0000-0000-7000-8000-000000000778"
    _insert_artifact(migrated_engine, artifact_id)
    success_lease = runner.claim("worker-a", NOW)
    assert success_lease is not None
    completed = runner.complete(
        succeeded.id,
        artifact_id,
        success_lease.worker_id,
        success_lease.attempt_id,
        NOW + timedelta(seconds=1),
    )
    failed = runner.enqueue(JobKind.REVIEW, PROJECT_ID, CHAPTER_ID, "fail")
    fail_lease = runner.claim("worker-a", NOW)
    assert fail_lease is not None
    terminal = runner.fail(
        failed.id,
        ErrorRecord(code="INPUT_INVALID", summary="Bad chapter map", retryable=False),
        fail_lease.worker_id,
        fail_lease.attempt_id,
        now=NOW + timedelta(seconds=1),
    )

    assert completed.status is JobStatus.SUCCEEDED
    assert completed.result_artifact_id == artifact_id
    assert completed.lease_owner is None
    assert completed.lease_expires_at is None
    assert terminal.status is JobStatus.FAILED
    assert terminal.error_code == "INPUT_INVALID"
    assert terminal.lease_owner is None
    assert terminal.lease_expires_at is None
    assert _attempt_rows(migrated_engine, succeeded.id)[-1][2] == "SUCCEEDED"
    assert _attempt_rows(migrated_engine, failed.id)[-1][2] == "FAILED"


def test_complete_requires_a_current_live_lease_and_preserves_unclaimed_job(
    runner: JobRunner,
    migrated_engine: Engine,
) -> None:
    job = runner.enqueue(JobKind.EXPORT, PROJECT_ID, CHAPTER_ID, "complete-unclaimed")
    artifact_id = "018f0000-0000-7000-8000-000000000779"
    _insert_artifact(migrated_engine, artifact_id)

    with pytest.raises(InvalidJobTransition, match="RUNNING"):
        runner.complete(job.id, artifact_id, "worker-a", "missing-attempt", NOW)

    view = runner.get(job.id)
    assert view.status is JobStatus.QUEUED
    assert view.result_artifact_id is None
    assert _attempt_rows(migrated_engine, job.id) == []


def test_complete_rejects_cross_worker_wrong_attempt_and_expired_lease_without_mutation(
    runner: JobRunner,
    migrated_engine: Engine,
) -> None:
    job = runner.enqueue(JobKind.EXPORT, PROJECT_ID, CHAPTER_ID, "complete-stale-lease")
    artifact_id = "018f0000-0000-7000-8000-000000000780"
    _insert_artifact(migrated_engine, artifact_id)
    lease = runner.claim("worker-a", NOW)
    assert lease is not None

    with pytest.raises(LeaseLost, match="worker"):
        runner.complete(job.id, artifact_id, "worker-b", lease.attempt_id, NOW + timedelta(seconds=1))
    with pytest.raises(LeaseLost, match="attempt"):
        runner.complete(job.id, artifact_id, lease.worker_id, "wrong-attempt", NOW + timedelta(seconds=1))
    with pytest.raises(LeaseLost, match="expired"):
        runner.complete(job.id, artifact_id, lease.worker_id, lease.attempt_id, NOW + timedelta(seconds=LEASE_SECONDS + 1))

    view = runner.get(job.id)
    assert view.status is JobStatus.RUNNING
    assert view.lease_owner == "worker-a"
    assert view.result_artifact_id is None
    assert _attempt_rows(migrated_engine, job.id) == [(1, None, None, None)]


@pytest.mark.parametrize("terminal_status", [JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.BILLING_UNKNOWN])
def test_complete_cannot_overwrite_terminal_or_billing_unknown_jobs(
    runner: JobRunner,
    migrated_engine: Engine,
    terminal_status: JobStatus,
) -> None:
    job = runner.enqueue(JobKind.EXPORT, PROJECT_ID, CHAPTER_ID, f"complete-terminal-{terminal_status.value}")
    first_artifact_id = f"018f0000-0000-7000-8000-0000000008{len(terminal_status.value):02d}"
    replacement_artifact_id = f"018f0000-0000-7000-8000-0000000009{len(terminal_status.value):02d}"
    _insert_artifact(migrated_engine, first_artifact_id)
    _insert_artifact(migrated_engine, replacement_artifact_id)
    lease = runner.claim("worker-a", NOW)
    assert lease is not None
    if terminal_status is JobStatus.SUCCEEDED:
        runner.complete(job.id, first_artifact_id, lease.worker_id, lease.attempt_id, NOW + timedelta(seconds=1))
    elif terminal_status is JobStatus.BILLING_UNKNOWN:
        runner.mark_provider_sent(job.id, "provider-request-1")
        runner.fail(
            job.id,
            ErrorRecord(code="PROVIDER_NETWORK", summary="timeout", retryable=True),
            lease.worker_id,
            lease.attempt_id,
            now=NOW + timedelta(seconds=1),
        )
    else:
        runner.fail(
            job.id,
            ErrorRecord(code="INPUT_INVALID", summary="bad input", retryable=False),
            lease.worker_id,
            lease.attempt_id,
            now=NOW + timedelta(seconds=1),
        )

    with pytest.raises(InvalidJobTransition):
        runner.complete(job.id, replacement_artifact_id, lease.worker_id, lease.attempt_id, NOW + timedelta(seconds=2))

    view = runner.get(job.id)
    assert view.status is terminal_status
    assert view.result_artifact_id == (first_artifact_id if terminal_status is JobStatus.SUCCEEDED else None)


def test_fail_requires_current_live_lease_and_preserves_wrong_worker_attempts(
    runner: JobRunner,
    migrated_engine: Engine,
) -> None:
    job = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "fail-stale-lease")
    lease = runner.claim("worker-a", NOW)
    assert lease is not None
    error = ErrorRecord(code="INPUT_INVALID", summary="bad input", retryable=False)

    with pytest.raises(LeaseLost, match="worker"):
        runner.fail(job.id, error, "worker-b", lease.attempt_id, now=NOW + timedelta(seconds=1))
    with pytest.raises(LeaseLost, match="attempt"):
        runner.fail(job.id, error, lease.worker_id, "wrong-attempt", now=NOW + timedelta(seconds=1))
    with pytest.raises(LeaseLost, match="expired"):
        runner.fail(job.id, error, lease.worker_id, lease.attempt_id, now=NOW + timedelta(seconds=LEASE_SECONDS + 1))

    view = runner.get(job.id)
    assert view.status is JobStatus.RUNNING
    assert view.error_code is None
    assert _attempt_rows(migrated_engine, job.id) == [(1, None, None, None)]


def test_cancel_requested_jobs_reject_terminal_worker_callbacks_without_closing_attempt(
    runner: JobRunner,
    migrated_engine: Engine,
) -> None:
    complete_job = runner.enqueue(JobKind.EXPORT, PROJECT_ID, CHAPTER_ID, "cancel-before-complete", priority=1)
    fail_job = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "cancel-before-fail", priority=2)
    artifact_id = "018f0000-0000-7000-8000-000000000781"
    _insert_artifact(migrated_engine, artifact_id)
    complete_lease = runner.claim("worker-a", NOW)
    assert complete_lease is not None
    fail_lease = runner.claim("worker-b", NOW)
    assert fail_lease is not None
    runner.request_cancel(complete_job.id, NOW + timedelta(seconds=1))
    runner.request_cancel(fail_job.id, NOW + timedelta(seconds=1))

    with pytest.raises(InvalidJobTransition, match="RUNNING"):
        runner.complete(
            complete_job.id,
            artifact_id,
            complete_lease.worker_id,
            complete_lease.attempt_id,
            NOW + timedelta(seconds=2),
        )
    with pytest.raises(InvalidJobTransition, match="RUNNING"):
        runner.fail(
            fail_job.id,
            ErrorRecord(code="INPUT_INVALID", summary="bad input", retryable=False),
            fail_lease.worker_id,
            fail_lease.attempt_id,
            now=NOW + timedelta(seconds=2),
        )

    assert runner.get(complete_job.id).status is JobStatus.CANCEL_REQUESTED
    assert runner.get(fail_job.id).status is JobStatus.CANCEL_REQUESTED
    assert _open_attempt_count(migrated_engine, complete_job.id) == 1
    assert _open_attempt_count(migrated_engine, fail_job.id) == 1


def test_acknowledge_cancel_closes_only_current_cancel_requested_lease(
    runner: JobRunner,
    migrated_engine: Engine,
) -> None:
    job = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "ack-cancel")
    lease = runner.claim("worker-a", NOW)
    assert lease is not None
    runner.request_cancel(job.id, NOW + timedelta(seconds=1))

    canceled = runner.acknowledge_cancel(job.id, lease.worker_id, lease.attempt_id, NOW + timedelta(seconds=2))

    assert canceled.status is JobStatus.CANCELED
    assert canceled.cancel_requested_at == NOW + timedelta(seconds=1)
    assert canceled.lease_owner is None
    assert canceled.lease_expires_at is None
    assert _attempt_rows(migrated_engine, job.id)[-1][2] == "CANCELED"
    assert _open_attempt_count(migrated_engine, job.id) == 0


def test_acknowledge_cancel_rejects_wrong_status_worker_attempt_and_expired_lease_without_mutation(
    runner: JobRunner,
    migrated_engine: Engine,
) -> None:
    running_job = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "ack-running")
    running_lease = runner.claim("worker-a", NOW)
    assert running_lease is not None
    with pytest.raises(InvalidJobTransition, match="CANCEL_REQUESTED"):
        runner.acknowledge_cancel(
            running_job.id,
            running_lease.worker_id,
            running_lease.attempt_id,
            NOW + timedelta(seconds=1),
        )
    assert runner.get(running_job.id).status is JobStatus.RUNNING
    assert _open_attempt_count(migrated_engine, running_job.id) == 1

    cancel_job = runner.enqueue(JobKind.REVIEW, PROJECT_ID, CHAPTER_ID, "ack-reject")
    cancel_lease = runner.claim("worker-b", NOW)
    assert cancel_lease is not None
    runner.request_cancel(cancel_job.id, NOW + timedelta(seconds=1))

    with pytest.raises(LeaseLost, match="worker"):
        runner.acknowledge_cancel(cancel_job.id, "other-worker", cancel_lease.attempt_id, NOW + timedelta(seconds=2))
    with pytest.raises(LeaseLost, match="attempt"):
        runner.acknowledge_cancel(cancel_job.id, cancel_lease.worker_id, "wrong-attempt", NOW + timedelta(seconds=2))
    with pytest.raises(LeaseLost, match="expired"):
        runner.acknowledge_cancel(
            cancel_job.id,
            cancel_lease.worker_id,
            cancel_lease.attempt_id,
            NOW + timedelta(seconds=LEASE_SECONDS + 1),
        )

    view = runner.get(cancel_job.id)
    assert view.status is JobStatus.CANCEL_REQUESTED
    assert view.lease_owner == cancel_lease.worker_id
    assert _attempt_rows(migrated_engine, cancel_job.id) == [(1, None, None, None)]


@pytest.mark.parametrize(
    ("code", "attempt_no", "retry_after", "expected"),
    [
        ("PROVIDER_NETWORK", 1, None, RetryDecision(RetryDisposition.RETRY, 2)),
        ("PROVIDER_5XX", 2, None, RetryDecision(RetryDisposition.RETRY, 10)),
        ("DB_BUSY", 3, None, RetryDecision(RetryDisposition.RETRY, 30)),
        ("PROVIDER_RATE_LIMIT", 1, 900, RetryDecision(RetryDisposition.RETRY, 600)),
        ("PROVIDER_RATE_LIMIT", 1, 30, RetryDecision(RetryDisposition.RETRY, 30)),
        ("PROVIDER_RATE_LIMIT", 1, None, RetryDecision(RetryDisposition.FAIL, None)),
        ("PROVIDER_RATE_LIMIT", 1, -1, RetryDecision(RetryDisposition.FAIL, None)),
        ("PROVIDER_AUTH", 1, None, RetryDecision(RetryDisposition.FAIL, None)),
        ("PROVIDER_QUOTA", 1, None, RetryDecision(RetryDisposition.FAIL, None)),
        ("BUDGET_LIMIT_EXCEEDED", 1, None, RetryDecision(RetryDisposition.FAIL, None)),
        ("MODEL_LICENSE_UNVERIFIED", 1, None, RetryDecision(RetryDisposition.FAIL, None)),
        ("INPUT_INVALID", 1, None, RetryDecision(RetryDisposition.FAIL, None)),
        ("PROVIDER_SCHEMA", 1, None, RetryDecision(RetryDisposition.FAIL, None)),
        ("DETERMINISTIC_QA_FAILED", 1, None, RetryDecision(RetryDisposition.FAIL, None)),
        ("PROVIDER_NETWORK", 4, None, RetryDecision(RetryDisposition.FAIL, None)),
    ],
)
def test_retry_policy_is_deterministic_and_exhausts_attempts(
    code: str,
    attempt_no: int,
    retry_after: int | None,
    expected: RetryDecision,
) -> None:
    assert classify_retry(code, attempt_no=attempt_no, retry_after_seconds=retry_after) == expected


def test_fail_requeues_definitely_unsent_local_retryable_error(runner: JobRunner) -> None:
    job = runner.enqueue(JobKind.MASTER, PROJECT_ID, CHAPTER_ID, "local-retry")
    lease = runner.claim("worker-a", NOW)
    assert lease is not None

    view = runner.fail(
        job.id,
        ErrorRecord(code="DB_BUSY", summary="database is locked", retryable=True, provider_request_sent=False),
        lease.worker_id,
        lease.attempt_id,
        now=NOW + timedelta(seconds=1),
    )

    assert view.status is JobStatus.QUEUED
    assert view.next_run_at == NOW + timedelta(seconds=3)
    assert view.lease_owner is None
    assert view.lease_expires_at is None
    assert runner.claim("worker-b", NOW + timedelta(seconds=2)) is None
    assert runner.claim("worker-b", NOW + timedelta(seconds=3)).attempt_no == 2


def test_expired_local_retryable_attempt_requeues_without_new_attempt_until_claim(runner: JobRunner) -> None:
    job = runner.enqueue(JobKind.MASTER, PROJECT_ID, CHAPTER_ID, "expired-local")
    lease = runner.claim("worker-a", NOW)
    assert lease is not None
    recovery_now = NOW + timedelta(seconds=LEASE_SECONDS + RECOVERY_THRESHOLD_SECONDS + 1)

    recovered = runner.recover_expired(recovery_now)

    assert recovered == [job.id]
    assert runner.get(job.id).status is JobStatus.QUEUED
    assert runner.get(job.id).next_run_at == recovery_now + timedelta(seconds=2)
    assert _attempt_rows(runner.engine, job.id)[-1][2] == "EXPIRED_RETRY"
    assert runner.claim("worker-b", recovery_now) is None
    assert runner.claim("worker-b", recovery_now + timedelta(seconds=2)).attempt_no == 2


@pytest.mark.parametrize(
    ("attempt_count", "delay_seconds"),
    [(1, 2), (2, 10), (3, 30)],
)
def test_expired_local_recovery_uses_attempt_numbered_retry_schedule(
    runner: JobRunner,
    attempt_count: int,
    delay_seconds: int,
) -> None:
    job = runner.enqueue(JobKind.MASTER, PROJECT_ID, CHAPTER_ID, f"expired-local-{attempt_count}")
    lease = runner.claim("worker-a", NOW)
    assert lease is not None
    for attempt_no in range(1, attempt_count):
        failed = runner.fail(
            job.id,
            ErrorRecord(code="DB_BUSY", summary=f"busy {attempt_no}", retryable=True),
            lease.worker_id,
            lease.attempt_id,
            now=NOW + timedelta(seconds=attempt_no),
        )
        assert failed.status is JobStatus.QUEUED
        next_lease = runner.claim("worker-a", failed.next_run_at)
        assert next_lease is not None
        lease = next_lease

    recovery_now = lease.lease_expires_at + timedelta(seconds=RECOVERY_THRESHOLD_SECONDS + 1)
    assert runner.recover_expired(recovery_now) == [job.id]

    view = runner.get(job.id)
    assert view.status is JobStatus.QUEUED
    assert view.next_run_at == recovery_now + timedelta(seconds=delay_seconds)


def test_expired_local_attempt_four_becomes_failed_without_attempt_five(runner: JobRunner) -> None:
    job = runner.enqueue(JobKind.MASTER, PROJECT_ID, CHAPTER_ID, "expired-local-exhausted")
    lease = runner.claim("worker-a", NOW)
    assert lease is not None
    for attempt_no in range(1, 4):
        retried = runner.fail(
            job.id,
            ErrorRecord(code="DB_BUSY", summary=f"busy {attempt_no}", retryable=True),
            lease.worker_id,
            lease.attempt_id,
            now=NOW + timedelta(seconds=attempt_no),
        )
        lease = runner.claim("worker-a", retried.next_run_at)
        assert lease is not None

    recovery_now = lease.lease_expires_at + timedelta(seconds=RECOVERY_THRESHOLD_SECONDS + 1)
    assert runner.recover_expired(recovery_now) == [job.id]

    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.next_run_at is None
    assert runner.claim("worker-b", recovery_now + timedelta(seconds=30)) is None
    assert _attempt_rows(runner.engine, job.id)[-1][2] == "FAILED"
    assert len(_attempt_rows(runner.engine, job.id)) == 4


def test_expired_cloud_sent_attempt_becomes_billing_unknown_and_never_auto_retries(
    runner: JobRunner,
    migrated_engine: Engine,
) -> None:
    job = runner.enqueue(JobKind.SYNTHESIZE, PROJECT_ID, CHAPTER_ID, "expired-cloud")
    lease = runner.claim("worker-a", NOW)
    assert lease is not None
    runner.mark_provider_sent(job.id, "provider-request-1")
    recovery_now = NOW + timedelta(seconds=LEASE_SECONDS + RECOVERY_THRESHOLD_SECONDS + 1)

    recovered = runner.recover_expired(recovery_now)

    assert recovered == [job.id]
    view = runner.get(job.id)
    assert view.status is JobStatus.BILLING_UNKNOWN
    assert view.lease_owner is None
    assert view.lease_expires_at is None
    assert runner.claim("worker-b", recovery_now + timedelta(seconds=1)) is None
    assert _attempt_rows(migrated_engine, job.id)[-1] == (
        1,
        recovery_now.isoformat(),
        "BILLING_UNKNOWN",
        "provider-request-1",
    )


def test_expired_cancel_requested_local_attempt_is_recovered_as_canceled(
    runner: JobRunner,
    migrated_engine: Engine,
) -> None:
    job = runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "recover-cancel-local")
    lease = runner.claim("worker-a", NOW)
    assert lease is not None
    runner.request_cancel(job.id, NOW + timedelta(seconds=1))
    recovery_now = NOW + timedelta(seconds=LEASE_SECONDS + RECOVERY_THRESHOLD_SECONDS + 1)

    recovered = runner.recover_expired(recovery_now)

    assert recovered == [job.id]
    view = runner.get(job.id)
    assert view.status is JobStatus.CANCELED
    assert view.lease_owner is None
    assert view.lease_expires_at is None
    assert runner.claim("worker-b", recovery_now + timedelta(seconds=1)) is None
    assert _attempt_rows(migrated_engine, job.id)[-1] == (1, recovery_now.isoformat(), "CANCELED", None)
    assert _open_attempt_count(migrated_engine, job.id) == 0


def test_expired_cancel_requested_provider_sent_attempt_becomes_billing_unknown(
    runner: JobRunner,
    migrated_engine: Engine,
) -> None:
    job = runner.enqueue(JobKind.SYNTHESIZE, PROJECT_ID, CHAPTER_ID, "recover-cancel-provider")
    lease = runner.claim("worker-a", NOW)
    assert lease is not None
    runner.mark_provider_sent(job.id, "provider-request-2")
    runner.request_cancel(job.id, NOW + timedelta(seconds=1))
    recovery_now = NOW + timedelta(seconds=LEASE_SECONDS + RECOVERY_THRESHOLD_SECONDS + 1)

    recovered = runner.recover_expired(recovery_now)

    assert recovered == [job.id]
    view = runner.get(job.id)
    assert view.status is JobStatus.BILLING_UNKNOWN
    assert view.lease_owner is None
    assert view.lease_expires_at is None
    assert _attempt_rows(migrated_engine, job.id)[-1] == (
        1,
        recovery_now.isoformat(),
        "BILLING_UNKNOWN",
        "provider-request-2",
    )


def test_runner_rejects_naive_now_values(runner: JobRunner) -> None:
    naive = datetime(2026, 8, 19, 2, 0, 0)
    runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "naive-now")

    with pytest.raises(ValueError, match="timezone-aware"):
        runner.claim("worker-a", naive)
    with pytest.raises(ValueError, match="timezone-aware"):
        runner.heartbeat("missing", "worker-a", naive)
    with pytest.raises(ValueError, match="timezone-aware"):
        runner.request_cancel("missing", naive)
    with pytest.raises(ValueError, match="timezone-aware"):
        runner.recover_expired(naive)
    with pytest.raises(ValueError, match="timezone-aware"):
        runner.complete("missing", "missing", "worker-a", "attempt-a", naive)
    with pytest.raises(ValueError, match="timezone-aware"):
        runner.fail(
            "missing",
            ErrorRecord(code="INPUT_INVALID", summary="bad input", retryable=False),
            "worker-a",
            "attempt-a",
            now=naive,
        )
