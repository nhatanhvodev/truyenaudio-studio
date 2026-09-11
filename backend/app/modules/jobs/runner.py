from __future__ import annotations

import json

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable

from sqlalchemy import Engine, RowMapping, text

from app.contracts import JobKind, JobStatus, new_id
from app.modules.jobs.retry import RetryDisposition, classify_retry


LEASE_SECONDS = 60
HEARTBEAT_INTERVAL_SECONDS = 15
RECOVERY_THRESHOLD_SECONDS = 90


TERMINAL_STATUSES = {
    JobStatus.SUCCEEDED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELED.value,
    JobStatus.BLOCKED_BUDGET.value,
    JobStatus.BILLING_UNKNOWN.value,
}


class JobRunnerError(Exception):
    """Base exception for durable queue transition failures."""


class LeaseLost(JobRunnerError):
    """Raised when a worker no longer owns the live attempt lease."""


class InvalidJobTransition(JobRunnerError):
    """Raised when a job state cannot accept the requested transition."""


@dataclass(frozen=True)
class ErrorRecord:
    code: str
    summary: str
    retryable: bool
    provider_request_sent: bool = False
    retry_after_seconds: int | None = None
    redacted_detail: str | None = None


@dataclass(frozen=True)
class JobView:
    id: str
    kind: JobKind
    status: JobStatus
    project_id: str
    chapter_id: str | None
    idempotency_key: str
    priority: int
    progress_current: int
    progress_total: int
    cancel_requested_at: datetime | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    next_run_at: datetime | None
    result_artifact_id: str | None
    error_code: str | None
    error_summary: str | None
    plan: Mapping[str, object] | None = None


@dataclass(frozen=True)
class JobLease:
    job_id: str
    kind: JobKind
    worker_id: str
    attempt_id: str
    attempt_no: int
    lease_expires_at: datetime
    heartbeat_at: datetime


@dataclass(frozen=True)
class HeartbeatView:
    job_id: str
    worker_id: str
    heartbeat_at: datetime
    lease_expires_at: datetime


class JobRunner:
    def __init__(self, engine: Engine, *, id_factory: Callable[[], str] = new_id) -> None:
        self.engine = engine
        self._id_factory = id_factory

    def enqueue(
        self,
        kind: JobKind,
        project_id: str,
        chapter_id: str | None,
        idempotency_key: str,
        priority: int = 100,
        plan: Mapping[str, object] | None = None,
    ) -> JobView:
        now = datetime.now(UTC)
        job_id = self._id_factory()
        with self.engine.begin() as connection:
            insert_result = connection.execute(
                text(
                    """
                    INSERT OR IGNORE INTO jobs
                    (id, kind, status, project_id, chapter_id, idempotency_key, priority,
                     progress_current, progress_total, created_at, updated_at)
                    VALUES
                    (:id, :kind, :status, :project_id, :chapter_id, :idempotency_key, :priority,
                     0, 0, :now, :now)
                    """
                ),
                {
                    "id": job_id,
                    "kind": kind.value,
                    "status": JobStatus.QUEUED.value,
                    "project_id": project_id,
                    "chapter_id": chapter_id,
                    "idempotency_key": idempotency_key,
                    "priority": priority,
                    "now": now.isoformat(),
                },
            )
            if plan is not None and insert_result.rowcount > 0:
                connection.execute(
                    text("UPDATE jobs SET plan_json = :plan, updated_at = :now WHERE id = :id"),
                    {
                        "plan": _dump_plan(plan),
                        "now": now.isoformat(),
                        "id": job_id,
                    },
                )
            row = connection.execute(
                text(
                    """
                    SELECT * FROM jobs
                    WHERE project_id = :project_id
                      AND kind = :kind
                      AND idempotency_key = :idempotency_key
                    """
                ),
                {
                    "project_id": project_id,
                    "kind": kind.value,
                    "idempotency_key": idempotency_key,
                },
            ).mappings().one()
        return _job_view(row)

    def get(self, job_id: str) -> JobView:
        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).mappings().one()
        return _job_view(row)

    def claim(self, worker_id: str, now: datetime) -> JobLease | None:
        _require_aware(now)
        lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                job = connection.execute(
                    text(
                        """
                        SELECT * FROM jobs
                        WHERE status = :queued
                          AND (next_run_at IS NULL OR next_run_at <= :now)
                        ORDER BY priority ASC, id ASC
                        LIMIT 1
                        """
                    ),
                    {"queued": JobStatus.QUEUED.value, "now": now.isoformat()},
                ).mappings().first()
                if job is None:
                    connection.commit()
                    return None

                attempt_no = (
                    connection.execute(
                        text("SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM job_attempts WHERE job_id = :job_id"),
                        {"job_id": job["id"]},
                    ).scalar_one()
                )
                attempt_id = self._id_factory()
                connection.execute(
                    text(
                        """
                        UPDATE jobs
                        SET status = :running,
                            lease_owner = :worker_id,
                            lease_expires_at = :lease_expires_at,
                            next_run_at = NULL,
                            error_code = NULL,
                            error_summary = NULL,
                            updated_at = :now
                        WHERE id = :job_id
                        """
                    ),
                    {
                        "running": JobStatus.RUNNING.value,
                        "worker_id": worker_id,
                        "lease_expires_at": lease_expires_at.isoformat(),
                        "now": now.isoformat(),
                        "job_id": job["id"],
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO job_attempts
                        (id, job_id, attempt_no, started_at, heartbeat_at, created_at, updated_at)
                        VALUES (:id, :job_id, :attempt_no, :now, :now, :now, :now)
                        """
                    ),
                    {"id": attempt_id, "job_id": job["id"], "attempt_no": attempt_no, "now": now.isoformat()},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return JobLease(
            job_id=job["id"],
            kind=JobKind(job["kind"]),
            worker_id=worker_id,
            attempt_id=attempt_id,
            attempt_no=attempt_no,
            lease_expires_at=lease_expires_at,
            heartbeat_at=now,
        )

    def heartbeat(self, job_id: str, worker_id: str, attempt_id: str, now: datetime) -> HeartbeatView | None:
        _require_aware(now)
        lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE jobs
                    SET lease_expires_at = :lease_expires_at,
                        updated_at = :now
                    WHERE id = :job_id
                      AND status IN (:running, :cancel_requested)
                      AND lease_owner = :worker_id
                      AND lease_expires_at >= :now
                      AND EXISTS (
                        SELECT 1
                        FROM job_attempts
                        WHERE job_attempts.job_id = jobs.id
                          AND job_attempts.id = :attempt_id
                          AND job_attempts.finished_at IS NULL
                          AND job_attempts.attempt_no = (
                            SELECT MAX(open_attempts.attempt_no)
                            FROM job_attempts AS open_attempts
                            WHERE open_attempts.job_id = jobs.id
                              AND open_attempts.finished_at IS NULL
                          )
                      )
                    """
                ),
                {
                    "lease_expires_at": lease_expires_at.isoformat(),
                    "now": now.isoformat(),
                    "job_id": job_id,
                    "attempt_id": attempt_id,
                    "running": JobStatus.RUNNING.value,
                    "cancel_requested": JobStatus.CANCEL_REQUESTED.value,
                    "worker_id": worker_id,
                },
            )
            if result.rowcount != 1:
                return None
            connection.execute(
                text(
                    """
                    UPDATE job_attempts
                    SET heartbeat_at = :now,
                        updated_at = :now
                    WHERE id = :attempt_id
                      AND job_id = :job_id
                      AND finished_at IS NULL
                    """
                ),
                {"attempt_id": attempt_id, "job_id": job_id, "now": now.isoformat()},
            )
            if result.rowcount != 1:
                raise LeaseLost(f"attempt {attempt_id} is no longer open for job {job_id}")
        return HeartbeatView(job_id, worker_id, now, lease_expires_at)

    def request_cancel(self, job_id: str, now: datetime) -> JobView | None:
        _require_aware(now)
        with self.engine.begin() as connection:
            row = connection.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).mappings().one_or_none()
            if row is None or row["status"] in TERMINAL_STATUSES:
                return None

            status = JobStatus(row["status"])
            next_status = JobStatus.CANCELED if status is JobStatus.QUEUED else JobStatus.CANCEL_REQUESTED
            result = connection.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = :status,
                        cancel_requested_at = :now,
                        updated_at = :now
                    WHERE id = :job_id
                      AND status NOT IN ('SUCCEEDED', 'FAILED', 'CANCELED', 'BLOCKED_BUDGET', 'BILLING_UNKNOWN')
                    """
                ),
                {"status": next_status.value, "now": now.isoformat(), "job_id": job_id},
            )
            if result.rowcount != 1:
                return None
            # A cancel that only changed the row would stay invisible to live
            # stream consumers, because a job keeps its feed marker (U07).
            _replace_job_event(connection, job_id, now)
            row = connection.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).mappings().one()
        return _job_view(row)

    def view_job(self, job_id: str) -> JobView | None:
        """Read-only snapshot of one job for API eligibility checks (U07)."""
        with self.engine.connect() as connection:
            row = connection.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).mappings().one_or_none()
        return None if row is None else _job_view(row)

    def retry_failed(self, job_id: str, now: datetime) -> JobView | None:
        """Re-enqueue a FAILED job for one fresh manual attempt (U07).

        Returns None unless the row was still FAILED at transition time, so the
        caller can map the outcome to a precise error code. The SAME row is
        reused (idempotency key untouched, so a duplicate enqueue is impossible
        and committed outputs/ledger of prior attempts stay intact), the stale
        error is cleared, and the feed marker is replaced so live consumers see
        the QUEUED transition. Prior attempts remain in job_attempts; the next
        claim simply creates attempt_no = max + 1.
        """
        _require_aware(now)
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = :queued,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        next_run_at = :now,
                        cancel_requested_at = NULL,
                        progress_current = 0,
                        error_code = NULL,
                        error_summary = NULL,
                        updated_at = :now
                    WHERE id = :job_id
                      AND status = :failed
                    """
                ),
                {
                    "queued": JobStatus.QUEUED.value,
                    "failed": JobStatus.FAILED.value,
                    "now": now.isoformat(),
                    "job_id": job_id,
                },
            )
            if result.rowcount != 1:
                return None
            _replace_job_event(connection, job_id, now)
            row = connection.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).mappings().one()
        return _job_view(row)

    def acknowledge_cancel(self, job_id: str, worker_id: str, attempt_id: str, now: datetime) -> JobView:
        _require_aware(now)
        with self.engine.begin() as connection:
            self._require_cancel_lease(connection, job_id, worker_id, attempt_id, now)
            self._close_attempt_by_id(connection, job_id, attempt_id, now, "CANCELED")
            row = self._set_terminal_for_cancel_lease(connection, job_id, worker_id, now)
        return _job_view(row)

    def complete(
        self,
        job_id: str,
        result_artifact_id: str | None,
        worker_id: str,
        attempt_id: str,
        now: datetime,
    ) -> JobView:
        _require_aware(now)
        with self.engine.begin() as connection:
            self._require_live_lease(connection, job_id, worker_id, attempt_id, now)
            self._close_attempt_by_id(connection, job_id, attempt_id, now, "SUCCEEDED")
            row = self._set_terminal_for_lease(
                connection,
                job_id,
                JobStatus.SUCCEEDED,
                worker_id,
                now,
                result_artifact_id=result_artifact_id,
            )
            _emit_job_event(connection, job_id, now)
        return _job_view(row)

    def fail(
        self,
        job_id: str,
        error: ErrorRecord,
        worker_id: str,
        attempt_id: str,
        *,
        now: datetime,
    ) -> JobView:
        _require_aware(now)
        with self.engine.begin() as connection:
            attempt = self._require_live_lease(connection, job_id, worker_id, attempt_id, now)
            attempt_no = int(attempt["attempt_no"])
            provider_request_id = attempt["provider_request_id"]
            provider_sent = error.provider_request_sent or provider_request_id is not None

            if provider_sent:
                self._close_attempt_by_id(
                    connection,
                    job_id,
                    attempt_id,
                    now,
                    "BILLING_UNKNOWN",
                    provider_request_id=provider_request_id,
                    error=error,
                )
                row = self._set_terminal_for_lease(
                    connection,
                    job_id,
                    JobStatus.BILLING_UNKNOWN,
                    worker_id,
                    now,
                    error_code="BILLING_UNKNOWN",
                    error_summary=error.summary,
                )
                _emit_job_event(connection, job_id, now)
                return _job_view(row)

            decision = classify_retry(
                error.code,
                attempt_no=attempt_no,
                retry_after_seconds=error.retry_after_seconds,
            )
            if error.retryable and decision.disposition is RetryDisposition.RETRY:
                next_run_at = now + timedelta(seconds=decision.delay_seconds or 0)
                self._close_attempt_by_id(connection, job_id, attempt_id, now, "RETRY", error=error)
                row = self._requeue_for_lease(connection, job_id, worker_id, now, next_run_at, error)
                return _job_view(row)

            self._close_attempt_by_id(connection, job_id, attempt_id, now, "FAILED", error=error)
            row = self._set_terminal_for_lease(
                connection,
                job_id,
                JobStatus.FAILED,
                worker_id,
                now,
                error_code=error.code,
                error_summary=error.summary,
            )
            _emit_job_event(connection, job_id, now)
        return _job_view(row)

    def defer(self, job_id: str, next_run_at: datetime) -> JobView:
        _require_aware(next_run_at)
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE jobs SET next_run_at = :next_run_at, updated_at = :now WHERE id = :job_id"),
                {"next_run_at": next_run_at.isoformat(), "now": datetime.now(UTC).isoformat(), "job_id": job_id},
            )
            row = connection.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).mappings().one()
        return _job_view(row)

    def mark_provider_sent(
        self,
        job_id: str,
        worker_id: str,
        attempt_id: str,
        provider_request_id: str,
    ) -> None:
        timestamp = datetime.now(UTC)
        with self.engine.begin() as connection:
            result = connection.execute(
                text(
                    """
                    UPDATE job_attempts
                    SET provider_request_id = :provider_request_id,
                        updated_at = :now
                    WHERE id = :attempt_id
                      AND job_id = :job_id
                      AND finished_at IS NULL
                      AND EXISTS (
                        SELECT 1
                        FROM jobs
                        WHERE jobs.id = job_attempts.job_id
                          AND jobs.status IN (:running, :cancel_requested)
                          AND jobs.lease_owner = :worker_id
                      )
                    """
                ),
                {
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "attempt_id": attempt_id,
                    "provider_request_id": provider_request_id,
                    "running": JobStatus.RUNNING.value,
                    "cancel_requested": JobStatus.CANCEL_REQUESTED.value,
                    "now": timestamp.isoformat(),
                },
            )
            if result.rowcount != 1:
                raise LeaseLost(f"provider-sent marker rejected for attempt {attempt_id} on job {job_id}")

    def recover_expired(self, now: datetime) -> list[str]:
        _require_aware(now)
        expired_before = now - timedelta(seconds=RECOVERY_THRESHOLD_SECONDS)
        recovered: list[str] = []
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    text(
                        """
                        SELECT *
                        FROM jobs
                        WHERE status IN (:running, :cancel_requested)
                          AND lease_expires_at IS NOT NULL
                          AND lease_expires_at <= :expired_before
                        ORDER BY priority ASC, id ASC
                        """
                    ),
                    {
                        "running": JobStatus.RUNNING.value,
                        "cancel_requested": JobStatus.CANCEL_REQUESTED.value,
                        "expired_before": expired_before.isoformat(),
                    },
                ).mappings().all()

                for row in rows:
                    job_id = row["id"]
                    provider_request_id = self._current_provider_request_id(connection, job_id)
                    attempt_no = self._current_attempt_no(connection, job_id)
                    status = JobStatus(row["status"])
                    if provider_request_id is not None:
                        self._close_open_attempt(
                            connection,
                            job_id,
                            now,
                            "BILLING_UNKNOWN",
                            provider_request_id=provider_request_id,
                        )
                        self._set_terminal(
                            connection,
                            job_id,
                            JobStatus.BILLING_UNKNOWN,
                            now,
                            error_code="BILLING_UNKNOWN",
                            error_summary="Provider request was sent before lease expired",
                        )
                    elif status is JobStatus.CANCEL_REQUESTED:
                        self._close_open_attempt(connection, job_id, now, "CANCELED")
                        self._set_terminal(connection, job_id, JobStatus.CANCELED, now)
                    else:
                        error = ErrorRecord(code="DB_BUSY", summary="Lease expired", retryable=True)
                        decision = classify_retry(error.code, attempt_no=attempt_no)
                        if decision.disposition is RetryDisposition.RETRY:
                            self._close_open_attempt(connection, job_id, now, "EXPIRED_RETRY")
                            self._requeue(
                                connection,
                                job_id,
                                now,
                                now + timedelta(seconds=decision.delay_seconds or 0),
                                error,
                            )
                        else:
                            self._close_open_attempt(connection, job_id, now, "FAILED", error=error)
                            self._set_terminal(
                                connection,
                                job_id,
                                JobStatus.FAILED,
                                now,
                                error_code=error.code,
                                error_summary=error.summary,
                            )
                    recovered.append(job_id)

                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return recovered

    def _current_attempt_no(self, connection, job_id: str) -> int:
        attempt_no = connection.execute(
            text("SELECT COALESCE(MAX(attempt_no), 0) FROM job_attempts WHERE job_id = :job_id"),
            {"job_id": job_id},
        ).scalar_one()
        return int(attempt_no)

    def _current_provider_request_id(self, connection, job_id: str) -> str | None:
        return connection.execute(
            text(
                """
                SELECT provider_request_id
                FROM job_attempts
                WHERE job_id = :job_id AND finished_at IS NULL
                ORDER BY attempt_no DESC
                LIMIT 1
                """
            ),
            {"job_id": job_id},
        ).scalar_one_or_none()

    def _require_live_lease(
        self,
        connection,
        job_id: str,
        worker_id: str,
        attempt_id: str,
        now: datetime,
    ) -> RowMapping:
        row = connection.execute(
            text(
                """
                SELECT
                    jobs.status,
                    jobs.lease_owner,
                    jobs.lease_expires_at,
                    job_attempts.id AS attempt_id,
                    job_attempts.attempt_no,
                    job_attempts.provider_request_id
                FROM jobs
                LEFT JOIN job_attempts
                  ON job_attempts.job_id = jobs.id
                 AND job_attempts.finished_at IS NULL
                 AND job_attempts.attempt_no = (
                    SELECT MAX(open_attempts.attempt_no)
                    FROM job_attempts AS open_attempts
                    WHERE open_attempts.job_id = jobs.id
                      AND open_attempts.finished_at IS NULL
                 )
                WHERE jobs.id = :job_id
                """
            ),
            {"job_id": job_id},
        ).mappings().one_or_none()
        if row is None:
            raise InvalidJobTransition(f"job {job_id} does not exist")
        if row["status"] != JobStatus.RUNNING.value:
            raise InvalidJobTransition(f"job {job_id} must be RUNNING for lease-scoped transition")
        if row["lease_owner"] != worker_id:
            raise LeaseLost(f"worker {worker_id} does not own job {job_id}")
        lease_expires_at = _parse_dt(row["lease_expires_at"])
        if lease_expires_at is None or lease_expires_at < now:
            raise LeaseLost(f"lease expired for job {job_id}")
        if row["attempt_id"] != attempt_id:
            raise LeaseLost(f"attempt {attempt_id} is not the current open attempt for job {job_id}")
        return row

    def _require_cancel_lease(
        self,
        connection,
        job_id: str,
        worker_id: str,
        attempt_id: str,
        now: datetime,
    ) -> RowMapping:
        row = connection.execute(
            text(
                """
                SELECT
                    jobs.status,
                    jobs.lease_owner,
                    jobs.lease_expires_at,
                    job_attempts.id AS attempt_id,
                    job_attempts.attempt_no,
                    job_attempts.provider_request_id
                FROM jobs
                LEFT JOIN job_attempts
                  ON job_attempts.job_id = jobs.id
                 AND job_attempts.finished_at IS NULL
                 AND job_attempts.attempt_no = (
                    SELECT MAX(open_attempts.attempt_no)
                    FROM job_attempts AS open_attempts
                    WHERE open_attempts.job_id = jobs.id
                      AND open_attempts.finished_at IS NULL
                 )
                WHERE jobs.id = :job_id
                """
            ),
            {"job_id": job_id},
        ).mappings().one_or_none()
        if row is None:
            raise InvalidJobTransition(f"job {job_id} does not exist")
        if row["status"] != JobStatus.CANCEL_REQUESTED.value:
            raise InvalidJobTransition(f"job {job_id} must be CANCEL_REQUESTED to acknowledge cancellation")
        if row["lease_owner"] != worker_id:
            raise LeaseLost(f"worker {worker_id} does not own job {job_id}")
        lease_expires_at = _parse_dt(row["lease_expires_at"])
        if lease_expires_at is None or lease_expires_at < now:
            raise LeaseLost(f"lease expired for job {job_id}")
        if row["attempt_id"] != attempt_id:
            raise LeaseLost(f"attempt {attempt_id} is not the current open attempt for job {job_id}")
        return row

    def _close_attempt_by_id(
        self,
        connection,
        job_id: str,
        attempt_id: str,
        now: datetime,
        outcome: str,
        *,
        provider_request_id: str | None = None,
        error: ErrorRecord | None = None,
    ) -> None:
        result = connection.execute(
            text(
                """
                UPDATE job_attempts
                SET finished_at = :now,
                    heartbeat_at = COALESCE(heartbeat_at, :now),
                    outcome = :outcome,
                    provider_request_id = COALESCE(:provider_request_id, provider_request_id),
                    error_class = :error_class,
                    redacted_detail = :redacted_detail,
                    updated_at = :now
                WHERE id = :attempt_id
                  AND job_id = :job_id
                  AND finished_at IS NULL
                """
            ),
            {
                "attempt_id": attempt_id,
                "job_id": job_id,
                "now": now.isoformat(),
                "outcome": outcome,
                "provider_request_id": provider_request_id,
                "error_class": error.code if error else None,
                "redacted_detail": error.redacted_detail if error else None,
            },
        )
        if result.rowcount != 1:
            raise LeaseLost(f"attempt {attempt_id} is no longer open for job {job_id}")

    def _close_open_attempt(
        self,
        connection,
        job_id: str,
        now: datetime,
        outcome: str,
        *,
        provider_request_id: str | None = None,
        error: ErrorRecord | None = None,
    ) -> None:
        connection.execute(
            text(
                """
                UPDATE job_attempts
                SET finished_at = :now,
                    heartbeat_at = COALESCE(heartbeat_at, :now),
                    outcome = :outcome,
                    provider_request_id = COALESCE(:provider_request_id, provider_request_id),
                    error_class = :error_class,
                    redacted_detail = :redacted_detail,
                    updated_at = :now
                WHERE id = (
                    SELECT id
                    FROM job_attempts
                    WHERE job_id = :job_id AND finished_at IS NULL
                    ORDER BY attempt_no DESC
                    LIMIT 1
                )
                """
            ),
            {
                "job_id": job_id,
                "now": now.isoformat(),
                "outcome": outcome,
                "provider_request_id": provider_request_id,
                "error_class": error.code if error else None,
                "redacted_detail": error.redacted_detail if error else None,
            },
        )

    def _requeue(
        self,
        connection,
        job_id: str,
        now: datetime,
        next_run_at: datetime,
        error: ErrorRecord,
    ) -> RowMapping:
        connection.execute(
            text(
                """
                UPDATE jobs
                SET status = :queued,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_run_at = :next_run_at,
                    error_code = :error_code,
                    error_summary = :error_summary,
                    updated_at = :now
                WHERE id = :job_id
                """
            ),
            {
                "queued": JobStatus.QUEUED.value,
                "next_run_at": next_run_at.isoformat(),
                "error_code": error.code,
                "error_summary": error.summary,
                "now": now.isoformat(),
                "job_id": job_id,
            },
        )
        return connection.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).mappings().one()

    def _requeue_for_lease(
        self,
        connection,
        job_id: str,
        worker_id: str,
        now: datetime,
        next_run_at: datetime,
        error: ErrorRecord,
    ) -> RowMapping:
        result = connection.execute(
            text(
                """
                UPDATE jobs
                SET status = :queued,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_run_at = :next_run_at,
                    error_code = :error_code,
                    error_summary = :error_summary,
                    updated_at = :now
                WHERE id = :job_id
                  AND status = :running
                  AND lease_owner = :worker_id
                  AND lease_expires_at >= :now
                """
            ),
            {
                "queued": JobStatus.QUEUED.value,
                "running": JobStatus.RUNNING.value,
                "worker_id": worker_id,
                "next_run_at": next_run_at.isoformat(),
                "error_code": error.code,
                "error_summary": error.summary,
                "now": now.isoformat(),
                "job_id": job_id,
            },
        )
        if result.rowcount != 1:
            raise LeaseLost(f"lease lost while requeueing job {job_id}")
        return connection.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).mappings().one()

    def _set_terminal(
        self,
        connection,
        job_id: str,
        status: JobStatus,
        now: datetime,
        *,
        result_artifact_id: str | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> RowMapping:
        connection.execute(
            text(
                """
                UPDATE jobs
                SET status = :status,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_run_at = NULL,
                    result_artifact_id = COALESCE(:result_artifact_id, result_artifact_id),
                    error_code = :error_code,
                    error_summary = :error_summary,
                    updated_at = :now
                WHERE id = :job_id
                """
            ),
            {
                "status": status.value,
                "result_artifact_id": result_artifact_id,
                "error_code": error_code,
                "error_summary": error_summary,
                "now": now.isoformat(),
                "job_id": job_id,
            },
        )
        return connection.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).mappings().one()

    def _set_terminal_for_lease(
        self,
        connection,
        job_id: str,
        status: JobStatus,
        worker_id: str,
        now: datetime,
        *,
        result_artifact_id: str | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> RowMapping:
        result = connection.execute(
            text(
                """
                UPDATE jobs
                SET status = :status,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_run_at = NULL,
                    result_artifact_id = COALESCE(:result_artifact_id, result_artifact_id),
                    error_code = :error_code,
                    error_summary = :error_summary,
                    updated_at = :now
                WHERE id = :job_id
                  AND status = :running
                  AND lease_owner = :worker_id
                  AND lease_expires_at >= :now
                """
            ),
            {
                "status": status.value,
                "running": JobStatus.RUNNING.value,
                "worker_id": worker_id,
                "result_artifact_id": result_artifact_id,
                "error_code": error_code,
                "error_summary": error_summary,
                "now": now.isoformat(),
                "job_id": job_id,
            },
        )
        if result.rowcount != 1:
            raise LeaseLost(f"lease lost while closing job {job_id}")
        return connection.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).mappings().one()

    def _set_terminal_for_cancel_lease(
        self,
        connection,
        job_id: str,
        worker_id: str,
        now: datetime,
    ) -> RowMapping:
        result = connection.execute(
            text(
                """
                UPDATE jobs
                SET status = :canceled,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_run_at = NULL,
                    error_code = NULL,
                    error_summary = NULL,
                    updated_at = :now
                WHERE id = :job_id
                  AND status = :cancel_requested
                  AND lease_owner = :worker_id
                  AND lease_expires_at >= :now
                """
            ),
            {
                "canceled": JobStatus.CANCELED.value,
                "cancel_requested": JobStatus.CANCEL_REQUESTED.value,
                "worker_id": worker_id,
                "now": now.isoformat(),
                "job_id": job_id,
            },
        )
        if result.rowcount != 1:
            raise LeaseLost(f"lease lost while canceling job {job_id}")
        return connection.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).mappings().one()


def _job_view(row: RowMapping) -> JobView:
    return JobView(
        id=row["id"],
        kind=JobKind(row["kind"]),
        status=JobStatus(row["status"]),
        project_id=row["project_id"],
        chapter_id=row["chapter_id"],
        idempotency_key=row["idempotency_key"],
        priority=row["priority"],
        progress_current=row["progress_current"],
        progress_total=row["progress_total"],
        cancel_requested_at=_parse_dt(row["cancel_requested_at"]),
        lease_owner=row["lease_owner"],
        lease_expires_at=_parse_dt(row["lease_expires_at"]),
        next_run_at=_parse_dt(row["next_run_at"]),
        result_artifact_id=row["result_artifact_id"],
        error_code=row["error_code"],
        error_summary=row["error_summary"],
        plan=_parse_plan(row.get("plan_json")),
    )


def _emit_job_event(connection, job_id: str, now: datetime) -> None:
    """Append the job to the event feed in the same transaction (J03).

    The feed is append-only and stores references; insertion is guarded so a
    terminal transition can never add the same (entity_type, entity_id) twice.
    """
    connection.execute(
        text(
            """
            INSERT INTO event_log (entity_type, entity_id, created_at)
            SELECT 'job', :job_id, :now
            WHERE NOT EXISTS (
                SELECT 1 FROM event_log
                WHERE entity_type = 'job' AND entity_id = :job_id
            )
            """
        ),
        {"job_id": job_id, "now": now.isoformat()},
    )


def _replace_job_event(connection, job_id: str, now: datetime) -> None:
    """Publish a newer feed marker for a job (U07).

    The J03 feed keeps one marker row per entity, so a transition that live
    stream consumers must observe (a manual retry) is published as a NEW row:
    inserting before removing the old one is what makes the sequence strictly
    greater than any cursor a client already holds (SQLite reuses max(rowid)+1,
    so deleting first could hand the replacement the cursor's own value).
    Migration 0006 dropped uq_event_log_entity, so an entity may briefly own two
    rows; the older rows are removed in the same transaction.
    """
    connection.execute(
        text(
            """
            INSERT INTO event_log (entity_type, entity_id, created_at)
            VALUES ('job', :job_id, :now)
            """
        ),
        {"job_id": job_id, "now": now.isoformat()},
    )
    connection.execute(
        text(
            """
            DELETE FROM event_log
            WHERE entity_type = 'job'
              AND entity_id = :job_id
              AND sequence_id < (
                  SELECT MAX(sequence_id) FROM event_log
                  WHERE entity_type = 'job' AND entity_id = :job_id
              )
            """
        ),
        {"job_id": job_id},
    )


def _dump_plan(plan: Mapping[str, object] | None) -> str | None:
    if plan is None:
        return None
    return json.dumps(dict(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_plan(value: object) -> Mapping[str, object] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _parse_dt(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime values must be timezone-aware")
