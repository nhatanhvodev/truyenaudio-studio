"""U07: per-job cancel and retry actions on the durable queue.

Two layers are covered:
- the API contract (named error codes, eligibility, idempotency, billing note);
- the runner transition `retry_failed` (same row requeued, stale error cleared,
  feed marker refreshed, prior attempt trail preserved).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Iterator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Engine, text

from app.contracts import JobKind, JobStatus, RightsStatus, SourceType
from app.db.base import create_engine_for
from app.main import create_app
from app.modules.jobs.runner import ErrorRecord, JobRunner
from app.settings.config import Settings


LOOPBACK_ORIGIN = "http://127.0.0.1:8765"
SEED_TIME = datetime(2026, 8, 20, 9, 0, 0, tzinfo=UTC)
PROJECT_ID = "018f0000-0000-7000-8000-000000000201"
CHAPTER_ID = "018f0000-0000-7000-8000-000000000202"
WORKER_ID = "test-worker"


@pytest.fixture
def engine(settings: Settings, migrated_engine: Engine) -> Iterator[Engine]:
    """The app's own database file, already migrated by the shared fixture.

    `settings.data_root` and `migrated_engine` both come from `tmp_path`, so
    the API client and the assertions below share one studio.sqlite3.
    """
    assert (settings.data_root / "studio.sqlite3").exists()
    connection = create_engine_for(settings.data_root / "studio.sqlite3")
    _seed_project(connection)
    try:
        yield connection
    finally:
        connection.dispose()


@pytest.fixture
def client(settings: Settings, engine: Engine) -> Iterator[TestClient]:
    # create_app never migrates by itself, so every API test runs against the
    # migrated database the `engine` fixture guarantees (same data_root).
    app = create_app(settings=settings, acquire_lock=False)
    with TestClient(app, base_url=LOOPBACK_ORIGIN) as test_client:
        yield test_client


@pytest.fixture
def headers(client: TestClient) -> dict[str, str]:
    token = client.get("/api/security/bootstrap").json()["csrfToken"]
    return {"Origin": LOOPBACK_ORIGIN, "X-CSRF-Token": token}


def _seed_project(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects (id, title, slug, source_type, rights_status, created_at, updated_at)
                VALUES (:id, 'Job Actions', 'job-actions', :source_type, :rights_status, :now, :now)
                """
            ),
            {
                "id": PROJECT_ID,
                "source_type": SourceType.SELF_AUTHORED.value,
                "rights_status": RightsStatus.PRIVATE_ONLY.value,
                "now": SEED_TIME.isoformat(),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO chapters (id, project_id, ordinal, state, created_at, updated_at)
                VALUES (:id, :project_id, 1, 'IMPORTED', :now, :now)
                """
            ),
            {"id": CHAPTER_ID, "project_id": PROJECT_ID, "now": SEED_TIME.isoformat()},
        )


def _enqueue(engine: Engine, key: str) -> str:
    return JobRunner(engine).enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, key).id


def _status_of(engine: Engine, job_id: str) -> tuple[str, str | None, int]:
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT status, error_code, progress_current FROM jobs WHERE id = :id"),
            {"id": job_id},
        ).one()
    return row[0], row[1], row[2]


def _run_once(engine: Engine, job_id: str, error: ErrorRecord, now: datetime) -> None:
    """Claim the queued job and fail it exactly once."""
    runner = JobRunner(engine)
    lease = runner.claim(WORKER_ID, now)
    assert lease is not None and lease.job_id == job_id
    runner.fail(job_id, error, lease.worker_id, lease.attempt_id, now=now)


def _fail_permanently(engine: Engine, job_id: str, error: ErrorRecord, now: datetime) -> datetime:
    """Drive a job to FAILED, exhausting the automatic retry budget.

    A retryable error is requeued until the attempt budget runs out, so the job
    only becomes FAILED on the 4th attempt — the state a manual retry starts from.
    """
    for _ in range(4):
        _run_once(engine, job_id, error, now)
        now = now + timedelta(seconds=60)
    assert _status_of(engine, job_id)[0] == JobStatus.FAILED.value
    return now


def _feed_marker(engine: Engine, job_id: str) -> int | None:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT sequence_id FROM event_log WHERE entity_type = 'job' AND entity_id = :job_id"),
            {"job_id": job_id},
        ).scalar_one_or_none()


def test_cancel_queued_job_terminates_it(client: TestClient, headers: dict[str, str], engine: Engine) -> None:
    job_id = _enqueue(engine, "cancel-queued")

    response = client.post(f"/api/jobs/{job_id}/cancel", headers=headers)

    assert response.status_code == 200
    assert response.json()["jobId"] == job_id
    assert response.json()["status"] == JobStatus.CANCELED.value
    assert _status_of(engine, job_id)[0] == JobStatus.CANCELED.value


def test_cancel_running_job_requests_cancel_and_notes_provider_billing(
    client: TestClient, headers: dict[str, str], engine: Engine
) -> None:
    job_id = _enqueue(engine, "cancel-running")
    JobRunner(engine).claim(WORKER_ID, SEED_TIME)

    response = client.post(f"/api/jobs/{job_id}/cancel", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == JobStatus.CANCEL_REQUESTED.value
    # A client cancel is honoured at the next safe point; the provider may already
    # have billed for the work in flight (plan J02).
    assert response.json()["billingNote"] == "PROVIDER_BILLING_NOT_GUARANTEED"


def test_cancel_terminal_job_is_rejected_with_named_code(
    client: TestClient, headers: dict[str, str], engine: Engine
) -> None:
    job_id = _enqueue(engine, "cancel-terminal")
    _run_once(engine, job_id, ErrorRecord(code="HTTP_400", summary="bad request", retryable=False), SEED_TIME)

    response = client.post(f"/api/jobs/{job_id}/cancel", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "JOB_CANCEL_NOT_ELIGIBLE"
    assert _status_of(engine, job_id)[0] == JobStatus.FAILED.value


def test_cancel_unknown_job_is_not_found(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/api/jobs/018f0000-0000-7000-8000-00000000dead/cancel", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "JOB_NOT_FOUND"


def test_retry_requeues_exhausted_failure_and_clears_the_stale_error(
    client: TestClient, headers: dict[str, str], engine: Engine
) -> None:
    job_id = _enqueue(engine, "retry-requeue")
    _fail_permanently(
        engine, job_id, ErrorRecord(code="PROVIDER_5XX", summary="provider 503", retryable=True), SEED_TIME
    )

    response = client.post(f"/api/jobs/{job_id}/retry", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "jobId": job_id,
        "kind": JobKind.TRANSLATE.value,
        "status": JobStatus.QUEUED.value,
        "current": 0,
        "total": 0,
        "errorCode": None,
    }
    status, error_code, progress_current = _status_of(engine, job_id)
    assert (status, error_code, progress_current) == (JobStatus.QUEUED.value, None, 0)

    # The manual retry is a real new attempt, not a replay of the old one. The
    # route stamps next_run_at with the wall clock, so claim at wall-clock time.
    lease = JobRunner(engine).claim(WORKER_ID, datetime.now(UTC) + timedelta(seconds=1))
    assert lease is not None and lease.job_id == job_id
    assert lease.attempt_no == 5


def test_retry_visible_in_job_feed(client: TestClient, headers: dict[str, str], engine: Engine) -> None:
    job_id = _enqueue(engine, "retry-feed")
    _fail_permanently(
        engine, job_id, ErrorRecord(code="PROVIDER_NETWORK", summary="network down", retryable=True), SEED_TIME
    )

    assert client.post(f"/api/jobs/{job_id}/retry", headers=headers).status_code == 200

    events = client.get("/api/jobs/snapshot").json()["events"]
    latest = {event["jobId"]: event for event in events}[job_id]
    assert latest["status"] == JobStatus.QUEUED.value
    assert latest["errorCode"] is None


def test_retry_non_retryable_failure_is_rejected(client: TestClient, headers: dict[str, str], engine: Engine) -> None:
    job_id = _enqueue(engine, "retry-definitive")
    _run_once(engine, job_id, ErrorRecord(code="HTTP_400", summary="bad request", retryable=False), SEED_TIME)

    response = client.post(f"/api/jobs/{job_id}/retry", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "JOB_RETRY_NOT_RETRYABLE"
    assert _status_of(engine, job_id)[0] == JobStatus.FAILED.value


def test_retry_billing_unknown_is_never_resent(client: TestClient, headers: dict[str, str], engine: Engine) -> None:
    job_id = _enqueue(engine, "retry-billing-unknown")
    _run_once(
        engine,
        job_id,
        ErrorRecord(code="PROVIDER_TIMEOUT", summary="timeout after send", retryable=True, provider_request_sent=True),
        SEED_TIME,
    )
    assert _status_of(engine, job_id)[0] == JobStatus.BILLING_UNKNOWN.value

    response = client.post(f"/api/jobs/{job_id}/retry", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "JOB_RETRY_NOT_ELIGIBLE"
    assert _status_of(engine, job_id)[0] == JobStatus.BILLING_UNKNOWN.value


def test_retry_non_failed_job_is_rejected(client: TestClient, headers: dict[str, str], engine: Engine) -> None:
    job_id = _enqueue(engine, "retry-running")
    JobRunner(engine).claim(WORKER_ID, SEED_TIME)

    response = client.post(f"/api/jobs/{job_id}/retry", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "JOB_RETRY_NOT_ELIGIBLE"


def test_retry_unknown_job_is_not_found(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/api/jobs/018f0000-0000-7000-8000-00000000beef/retry", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "JOB_NOT_FOUND"


def test_retry_is_idempotent_and_never_duplicates_the_job(
    client: TestClient, headers: dict[str, str], engine: Engine
) -> None:
    job_id = _enqueue(engine, "retry-idempotent")
    _fail_permanently(engine, job_id, ErrorRecord(code="DB_BUSY", summary="database is locked", retryable=True), SEED_TIME)

    first = client.post(f"/api/jobs/{job_id}/retry", headers=headers)
    second = client.post(f"/api/jobs/{job_id}/retry", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"] == "JOB_RETRY_NOT_ELIGIBLE"
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT COUNT(*) FROM jobs WHERE project_id = :project_id"), {"project_id": PROJECT_ID}
        ).scalar_one()
    assert rows == 1
    assert _status_of(engine, job_id)[0] == JobStatus.QUEUED.value


def test_retry_failed_refreshes_the_feed_marker(
    engine: Engine, deterministic_uuid7_factory
) -> None:
    """A retry must be observable by live stream consumers, not just by the snapshot."""
    job_id = _enqueue(engine, "retry-marker")
    _run_once(engine, job_id, ErrorRecord(code="HTTP_400", summary="bad request", retryable=False), SEED_TIME)
    before = _feed_marker(engine, job_id)
    assert before is not None

    view = JobRunner(engine, id_factory=deterministic_uuid7_factory).retry_failed(job_id, SEED_TIME + timedelta(seconds=5))

    assert view is not None and view.status is JobStatus.QUEUED
    after = _feed_marker(engine, job_id)
    assert after is not None and after > before
    with engine.connect() as connection:
        markers = connection.execute(
            text("SELECT COUNT(*) FROM event_log WHERE entity_type = 'job' AND entity_id = :job_id"),
            {"job_id": job_id},
        ).scalar_one()
    assert markers == 1


def test_retry_failed_leaves_non_failed_jobs_untouched(engine: Engine, deterministic_uuid7_factory) -> None:
    job_id = _enqueue(engine, "retry-not-failed")

    view = JobRunner(engine, id_factory=deterministic_uuid7_factory).retry_failed(job_id, SEED_TIME)

    assert view is None
    assert _status_of(engine, job_id)[0] == JobStatus.QUEUED.value
