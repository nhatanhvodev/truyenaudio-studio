from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from uuid import uuid4

from app.contracts import JobKind, JobStatus
from app.db.base import create_engine_for
from app.db.base import session_factory
from app.modules.jobs.recovery import RecoveryCanceled, RecoveryJobContext, recover_expired
from app.modules.jobs.runner import HEARTBEAT_INTERVAL_SECONDS, ErrorRecord, JobLease, JobRunner
from app.settings.config import Settings
from app.settings.startup_lock import AlreadyRunning, StartupLock


Handler = Callable[..., Awaitable[str | None]]


class HandlerUnavailable(Exception):
    def __init__(self, kind: JobKind) -> None:
        self.code = f"{kind.value}_HANDLER_NOT_CONFIGURED"
        super().__init__(f"{kind.value} handler is not configured for the local worker")


@dataclass
class Worker:
    runner: JobRunner
    handlers: Mapping[JobKind, Handler]
    worker_id: str = "studio-worker-1"
    heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS
    clock: Callable[[], datetime] = field(default_factory=lambda: lambda: datetime.now(UTC))
    process_heartbeat_path: Path | None = None
    artifact_root: Path | None = None

    async def run_once(self) -> bool:
        now = self.clock()
        recover_expired(self.runner, now)
        lease = self.runner.claim(self.worker_id, now)
        if lease is None:
            self.refresh_process_heartbeat("idle")
            return False

        self.refresh_process_heartbeat("working", lease.kind)
        stop_heartbeats = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat_until_stopped(lease, stop_heartbeats))
        try:
            if self._cancel_requested(lease.job_id):
                self.runner.acknowledge_cancel(lease.job_id, lease.worker_id, lease.attempt_id, self.clock())
                self.refresh_process_heartbeat("idle")
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
                self.refresh_process_heartbeat("idle")
                return True

            try:
                result_artifact_id = await self._run_handler(handler, lease)
            except RecoveryCanceled:
                self.runner.acknowledge_cancel(lease.job_id, lease.worker_id, lease.attempt_id, self.clock())
                self.refresh_process_heartbeat("idle")
                return True
            except HandlerUnavailable as error:
                self.runner.fail(
                    lease.job_id,
                    ErrorRecord(
                        code=error.code,
                        summary=str(error),
                        retryable=False,
                    ),
                    lease.worker_id,
                    lease.attempt_id,
                    now=self.clock(),
                )
                self.refresh_process_heartbeat("idle")
                return True
            except Exception as error:
                self.runner.fail(
                    lease.job_id,
                    _redacted_error(error, lease.kind),
                    lease.worker_id,
                    lease.attempt_id,
                    now=self.clock(),
                )
                self.refresh_process_heartbeat("idle")
                return True

            if self._cancel_requested(lease.job_id):
                self.runner.acknowledge_cancel(lease.job_id, lease.worker_id, lease.attempt_id, self.clock())
                self.refresh_process_heartbeat("idle")
                return True

            self.runner.complete(lease.job_id, result_artifact_id, lease.worker_id, lease.attempt_id, self.clock())
            self.refresh_process_heartbeat("idle")
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
            if self.runner.heartbeat(lease.job_id, lease.worker_id, lease.attempt_id, self.clock()) is None:
                return

    def _cancel_requested(self, job_id: str) -> bool:
        return self.runner.get(job_id).status is JobStatus.CANCEL_REQUESTED

    async def _run_handler(self, handler: Handler, lease: JobLease) -> str | None:
        if len(inspect.signature(handler).parameters) < 2:
            return await handler(lease)
        if self.artifact_root is None:
            raise ValueError("WORKER_ARTIFACT_ROOT_REQUIRED")
        with session_factory(self.runner.engine)() as session:
            recovery = RecoveryJobContext(
                runner=self.runner,
                lease=lease,
                session=session,
                artifact_root=self.artifact_root,
            )
            try:
                return await handler(lease, recovery)
            except Exception:
                recovery.rollback()
                raise

    def refresh_process_heartbeat(self, status: str, job_kind: JobKind | None = None) -> None:
        if self.process_heartbeat_path is None:
            return
        payload = {
            "schema_version": "truyenaudio-studio.worker-heartbeat.v1",
            "worker_id": self.worker_id,
            "status": status,
            "updated_at": self.clock().isoformat(),
        }
        if job_kind is not None:
            payload["job_kind"] = job_kind.value
        _write_atomic_json(self.process_heartbeat_path, payload)


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
    return Worker(
        runner,
        handlers=build_default_handlers(settings),
        worker_id="studio-worker-1",
        artifact_root=settings.data_root / "artifacts",
        process_heartbeat_path=settings.data_root / "worker-heartbeat.json",
    )


def build_default_handlers(settings: Settings | None = None) -> dict[JobKind, Handler]:
    handlers = {kind: _unconfigured_recovery_handler for kind in JobKind}
    if settings is not None:
        from app.modules.jobs.execution_handlers import build_translate_handler

        handlers[JobKind.TRANSLATE] = build_translate_handler(settings)
    return handlers


async def _unconfigured_recovery_handler(lease: JobLease, recovery: RecoveryJobContext) -> str | None:
    recovery.raise_if_cancel_requested()
    raise HandlerUnavailable(lease.kind)


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


def _write_atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fd = os.open(partial_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(partial_path, path)
    finally:
        partial_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
