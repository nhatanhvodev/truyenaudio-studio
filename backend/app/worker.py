from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.contracts import JobKind, JobStatus
from app.db.base import create_engine_for
from app.modules.jobs.runner import HEARTBEAT_INTERVAL_SECONDS, ErrorRecord, JobLease, JobRunner
from app.settings.config import Settings
from app.settings.startup_lock import AlreadyRunning, StartupLock


Handler = Callable[[JobLease], Awaitable[str | None]]


@dataclass
class Worker:
    runner: JobRunner
    handlers: Mapping[JobKind, Handler]
    worker_id: str = "studio-worker-1"
    heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS
    clock: Callable[[], datetime] = field(default_factory=lambda: lambda: datetime.now(UTC))

    async def run_once(self) -> bool:
        lease = self.runner.claim(self.worker_id, self.clock())
        if lease is None:
            return False

        stop_heartbeats = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat_until_stopped(lease, stop_heartbeats))
        try:
            if self._cancel_requested(lease.job_id):
                self.runner.acknowledge_cancel(lease.job_id, lease.worker_id, lease.attempt_id, self.clock())
                return True

            handler = self.handlers.get(lease.kind)
            if handler is None:
                self.runner.fail(
                    lease.job_id,
                    ErrorRecord(
                        code="INPUT_UNSUPPORTED_JOB_KIND",
                        summary=f"No handler registered for {lease.kind.value}",
                        retryable=False,
                    ),
                    lease.worker_id,
                    lease.attempt_id,
                    now=self.clock(),
                )
                return True

            try:
                result_artifact_id = await handler(lease)
            except Exception as error:
                self.runner.fail(
                    lease.job_id,
                    _redacted_error(error, lease.kind),
                    lease.worker_id,
                    lease.attempt_id,
                    now=self.clock(),
                )
                return True

            if self._cancel_requested(lease.job_id):
                self.runner.acknowledge_cancel(lease.job_id, lease.worker_id, lease.attempt_id, self.clock())
                return True

            self.runner.complete(lease.job_id, result_artifact_id, lease.worker_id, lease.attempt_id, self.clock())
            return True
        finally:
            stop_heartbeats.set()
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

    async def _heartbeat_until_stopped(self, lease: JobLease, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.sleep(self.heartbeat_interval_seconds)
            if stop.is_set():
                return
            if self.runner.heartbeat(lease.job_id, lease.worker_id, self.clock()) is None:
                return

    def _cancel_requested(self, job_id: str) -> bool:
        return self.runner.get(job_id).status is JobStatus.CANCEL_REQUESTED


def _redacted_error(error: Exception, kind: JobKind) -> ErrorRecord:
    return ErrorRecord(
        code="WORKER_HANDLER_EXCEPTION",
        summary=f"{type(error).__name__} while processing {kind.value}",
        retryable=False,
        redacted_detail=type(error).__name__,
    )


async def worker_loop(worker: Worker, *, idle_sleep_seconds: float = 1.0) -> None:
    while True:
        worked = await worker.run_once()
        if not worked:
            await asyncio.sleep(idle_sleep_seconds)


def build_default_worker(settings: Settings | None = None) -> Worker:
    settings = settings or Settings()
    engine = create_engine_for(settings.data_root / "studio.sqlite3")
    runner = JobRunner(engine)
    return Worker(runner, handlers={}, worker_id="studio-worker-1")


def main() -> int:
    settings = Settings()
    lock_path = settings.data_root / "studio-worker.lock"
    try:
        with StartupLock(lock_path):
            asyncio.run(worker_loop(build_default_worker(settings)))
    except AlreadyRunning:
        return 0
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
