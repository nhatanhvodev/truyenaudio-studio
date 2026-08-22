from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.contracts import JobKind, JobStatus, new_id
from app.db.base import create_engine_for
from app.modules.diagnostics.service import DiagnosticsService, sha256_bytes
from app.modules.jobs.runner import JobLease, JobRunner
from app.settings.config import Settings
from app.worker import Worker


class DiagnosticExportRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    include_sample: bool = Field(default=False, alias="includeSample")
    sample_text: str | None = Field(default=None, alias="sampleText")


def create_diagnostics_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/diagnostics")
    active_settings = settings or Settings()

    @router.get("/health")
    def health() -> dict[str, object]:
        return DiagnosticsService(active_settings).health_snapshot()

    @router.post("/export")
    def export(request: DiagnosticExportRequest) -> Response:
        payload = DiagnosticsService(active_settings).export_zip(
            include_sample=request.include_sample,
            sample_text=request.sample_text,
        )
        return Response(
            payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="truyenaudio-diagnostics.zip"',
                "X-Diagnostics-Sha256": sha256_bytes(payload),
            },
        )

    @router.get("/fake-recovery/check")
    def fake_recovery_check(project_id: str = Query(alias="projectId")) -> dict[str, object]:
        if os.getenv("STUDIO_FAKE_AUDIO") != "1":
            raise HTTPException(status_code=404, detail="not found")
        return _fake_recovery_check(active_settings, project_id)

    @router.post("/fake-recovery/run")
    def fake_recovery_run(project_id: str = Query(alias="projectId")) -> dict[str, object]:
        if os.getenv("STUDIO_FAKE_AUDIO") != "1":
            raise HTTPException(status_code=404, detail="not found")
        return _fake_recovery_run(active_settings, project_id)

    return router


def _fake_recovery_check(settings: Settings, project_id: str) -> dict[str, object]:
    counts = _fake_recovery_counts(settings, project_id)
    return {
        "duplicateReadyCacheKeys": counts["duplicateReadyCacheKeys"],
        "duplicateReadyExportManifests": counts["duplicateReadyExportManifests"],
        "missingReadyArtifactCount": counts["missingReadyArtifactCount"],
    }


def _fake_recovery_run(settings: Settings, project_id: str) -> dict[str, object]:
    before = _fake_recovery_counts(settings, project_id)
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    job_id = _create_expired_recovery_job(settings, project_id, now)
    clock = _MutableClock(now)
    engine = create_engine_for(settings.data_root / "studio.sqlite3")
    runner = _CountingJobRunner(engine)

    async def complete_locally(_lease: JobLease) -> str | None:
        return None

    worker = Worker(
        runner,
        handlers={JobKind.EXPORT: complete_locally},
        worker_id="diagnostics-fake-worker",
        clock=clock,
        artifact_root=settings.data_root / "artifacts",
        process_heartbeat_path=settings.data_root / "worker-heartbeat.json",
    )
    try:
        worker_run_count = 0
        asyncio.run(worker.run_once())
        worker_run_count += 1
        clock.value = now + timedelta(seconds=3)
        asyncio.run(worker.run_once())
        worker_run_count += 1
        recovered_status = runner.get(job_id).status
    finally:
        engine.dispose()
    after = _fake_recovery_counts(settings, project_id)
    return {
        "workerRunCount": worker_run_count,
        "workerDrivenRecoveryCount": runner.recovered_count,
        "recoveredJobId": job_id,
        "recoveredJobStatus": recovered_status.value,
        "duplicateReadyCacheKeysBefore": before["duplicateReadyCacheKeys"],
        "duplicateReadyCacheKeysAfter": after["duplicateReadyCacheKeys"],
        "duplicateReadyExportManifestsBefore": before["duplicateReadyExportManifests"],
        "duplicateReadyExportManifestsAfter": after["duplicateReadyExportManifests"],
        "missingReadyArtifactCountBefore": before["missingReadyArtifactCount"],
        "missingReadyArtifactCountAfter": after["missingReadyArtifactCount"],
    }


def _fake_recovery_counts(settings: Settings, project_id: str) -> dict[str, object]:
    engine = create_engine_for(settings.data_root / "studio.sqlite3")
    try:
        with engine.connect() as connection:
            duplicate_cache_keys = [
                _digest_key(row["kind"], row["input_hash"], row["settings_hash"])
                for row in connection.execute(
                    text(
                        """
                        SELECT artifacts.kind, artifacts.input_hash, artifacts.settings_hash, COUNT(*) AS count
                        FROM artifacts
                        JOIN chapters ON chapters.id = artifacts.chapter_id
                        WHERE chapters.project_id = :project_id
                          AND artifacts.status = 'READY'
                        GROUP BY artifacts.kind, artifacts.input_hash, artifacts.settings_hash
                        HAVING COUNT(*) > 1
                        ORDER BY artifacts.kind, artifacts.input_hash, artifacts.settings_hash
                        """
                    ),
                    {"project_id": project_id},
                ).mappings()
            ]
            duplicate_export_manifests = [
                _digest_key(row["chapter_id"], row["kind"], row["manifest_sha256"])
                for row in connection.execute(
                    text(
                        """
                        SELECT exports.chapter_id, exports.kind, exports.manifest_sha256, COUNT(*) AS count
                        FROM exports
                        JOIN chapters ON chapters.id = exports.chapter_id
                        WHERE chapters.project_id = :project_id
                          AND exports.status = 'READY'
                          AND exports.manifest_sha256 IS NOT NULL
                        GROUP BY exports.chapter_id, exports.kind, exports.manifest_sha256
                        HAVING COUNT(*) > 1
                        ORDER BY exports.chapter_id, exports.kind, exports.manifest_sha256
                        """
                    ),
                    {"project_id": project_id},
                ).mappings()
            ]
            artifact_paths = [
                str(row["relative_path"])
                for row in connection.execute(
                    text(
                        """
                        SELECT artifacts.relative_path
                        FROM artifacts
                        JOIN chapters ON chapters.id = artifacts.chapter_id
                        WHERE chapters.project_id = :project_id
                          AND artifacts.status = 'READY'
                        """
                    ),
                    {"project_id": project_id},
                ).mappings()
            ]
    finally:
        engine.dispose()

    roots = (settings.data_root, settings.data_root / "artifacts")
    missing_count = sum(1 for relative_path in artifact_paths if not any((root / relative_path).is_file() for root in roots))
    return {
        "duplicateReadyCacheKeys": duplicate_cache_keys,
        "duplicateReadyExportManifests": duplicate_export_manifests,
        "missingReadyArtifactCount": missing_count,
    }


def _create_expired_recovery_job(settings: Settings, project_id: str, now: datetime) -> str:
    job_id = new_id()
    attempt_id = new_id()
    expired_at = now - timedelta(minutes=2)
    created_at = now - timedelta(minutes=4)
    engine = create_engine_for(settings.data_root / "studio.sqlite3")
    try:
        with engine.begin() as connection:
            project_exists = connection.execute(
                text("SELECT 1 FROM projects WHERE id = :project_id"),
                {"project_id": project_id},
            ).scalar_one_or_none()
            if project_exists is None:
                raise HTTPException(status_code=404, detail="PROJECT_NOT_FOUND")
            connection.execute(
                text(
                    """
                    INSERT INTO jobs
                    (id, kind, status, project_id, idempotency_key, priority,
                     progress_current, progress_total, lease_owner, lease_expires_at,
                     created_at, updated_at)
                    VALUES
                    (:id, :kind, :status, :project_id, :idempotency_key, 1,
                     0, 1, :worker_id, :lease_expires_at, :created_at, :created_at)
                    """
                ),
                {
                    "id": job_id,
                    "kind": JobKind.EXPORT.value,
                    "status": JobStatus.RUNNING.value,
                    "project_id": project_id,
                    "idempotency_key": f"diagnostics-recovery-{job_id}",
                    "worker_id": "dead-diagnostics-worker",
                    "lease_expires_at": expired_at.isoformat(),
                    "created_at": created_at.isoformat(),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO job_attempts
                    (id, job_id, attempt_no, started_at, heartbeat_at, created_at, updated_at)
                    VALUES
                    (:id, :job_id, 1, :started_at, :heartbeat_at, :started_at, :started_at)
                    """
                ),
                {
                    "id": attempt_id,
                    "job_id": job_id,
                    "started_at": created_at.isoformat(),
                    "heartbeat_at": expired_at.isoformat(),
                },
            )
    finally:
        engine.dispose()
    return job_id


def _digest_key(*parts: object) -> str:
    return hashlib.sha256(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()


class _CountingJobRunner(JobRunner):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.recovered_count = 0

    def recover_expired(self, now: datetime) -> list[str]:
        recovered = super().recover_expired(now)
        self.recovered_count += len(recovered)
        return recovered


class _MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value
