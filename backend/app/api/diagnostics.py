from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.contracts import ArtifactKind, ExportKind, JobKind, JobStatus, new_id
from app.db.base import create_engine_for
from app.modules.diagnostics.service import DiagnosticsService, sha256_bytes
from app.modules.jobs.recovery import RecoveryJobContext
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
    def fake_recovery_run(
        project_id: str = Query(alias="projectId"),
        chapter_id: str = Query(alias="chapterId"),
        job_id: str = Query(alias="jobId"),
    ) -> dict[str, object]:
        if os.getenv("STUDIO_FAKE_AUDIO") != "1":
            raise HTTPException(status_code=404, detail="not found")
        return _fake_recovery_run(active_settings, project_id, chapter_id, job_id)

    return router


def _fake_recovery_check(settings: Settings, project_id: str) -> dict[str, object]:
    counts = _fake_recovery_counts(settings, project_id)
    return {
        "duplicateReadyCacheKeys": counts["duplicateReadyCacheKeys"],
        "duplicateReadyExportManifests": counts["duplicateReadyExportManifests"],
        "missingReadyArtifactCount": counts["missingReadyArtifactCount"],
    }


def _fake_recovery_run(settings: Settings, project_id: str, chapter_id: str, job_id: str) -> dict[str, object]:
    before = _fake_recovery_counts(settings, project_id)
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    job_kind = _force_job_to_expired_running(settings, project_id, chapter_id, job_id, now)
    clock = _MutableClock(now)
    engine = create_engine_for(settings.data_root / "studio.sqlite3")
    runner = _CountingJobRunner(engine)

    async def complete_locally(lease: JobLease, recovery: RecoveryJobContext) -> str:
        if lease.job_id != job_id:
            raise RuntimeError("DIAGNOSTICS_RECOVERY_WRONG_JOB")
        payload = b"diagnostics fake recovery export"
        checkpoint = recovery.artifacts.checkpoint_ready_artifact(
            chapter_id=chapter_id,
            kind=ArtifactKind.PUBLICATION_BUNDLE,
            segment_id=lease.job_id,
            input_hash=hashlib.sha256(
                f"{project_id}:{chapter_id}:{job_id}:diagnostics-fake-recovery-input-v1".encode("utf-8")
            ).hexdigest(),
            settings_hash=hashlib.sha256(b"diagnostics-fake-recovery-settings-v1").hexdigest(),
            payload=payload,
            mime_type="application/zip",
            metadata={"diagnosticsFakeRecovery": True, "jobId": job_id},
            provider_sent=False,
            usage_committed=True,
        )
        recovery.artifacts.checkpoint_ready_export(
            chapter_id=chapter_id,
            kind=ExportKind.PUBLICATION_BUNDLE,
            manifest_sha256=hashlib.sha256(payload).hexdigest(),
            bundle_artifact_id=checkpoint.artifact_id,
            rights_evaluation={"diagnosticsFakeRecovery": True},
        )
        recovery.commit()
        return checkpoint.artifact_id

    worker = Worker(
        runner,
        handlers={job_kind: complete_locally},
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
        "recoveredProjectId": project_id,
        "recoveredChapterId": chapter_id,
        "recoveredJobKind": job_kind.value,
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


def _force_job_to_expired_running(
    settings: Settings,
    project_id: str,
    chapter_id: str,
    job_id: str,
    now: datetime,
) -> JobKind:
    attempt_id = new_id()
    expired_at = now - timedelta(minutes=2)
    created_at = now - timedelta(minutes=4)
    engine = create_engine_for(settings.data_root / "studio.sqlite3")
    try:
        with engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, kind, status, project_id, chapter_id
                    FROM jobs
                    WHERE id = :job_id
                    """
                ),
                {"job_id": job_id},
            ).mappings().one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="JOB_NOT_FOUND")
            if row["project_id"] != project_id or row["chapter_id"] != chapter_id:
                raise HTTPException(status_code=409, detail="JOB_PROJECT_CHAPTER_MISMATCH")
            job_kind = JobKind(row["kind"])
            if job_kind is not JobKind.EXPORT:
                raise HTTPException(status_code=409, detail="JOB_KIND_NOT_SUPPORTED")
            if row["status"] != JobStatus.QUEUED.value:
                raise HTTPException(status_code=409, detail="JOB_MUST_BE_QUEUED")
            attempt_no = int(
                connection.execute(
                    text("SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM job_attempts WHERE job_id = :job_id"),
                    {"job_id": job_id},
                ).scalar_one()
            )
            connection.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = :status,
                        lease_owner = :worker_id,
                        lease_expires_at = :lease_expires_at,
                        next_run_at = NULL,
                        progress_total = CASE WHEN progress_total < 1 THEN 1 ELSE progress_total END,
                        updated_at = :created_at
                    WHERE id = :job_id
                    """
                ),
                {
                    "status": JobStatus.RUNNING.value,
                    "worker_id": "dead-diagnostics-worker",
                    "lease_expires_at": expired_at.isoformat(),
                    "created_at": created_at.isoformat(),
                    "job_id": job_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO job_attempts
                    (id, job_id, attempt_no, started_at, heartbeat_at, created_at, updated_at)
                    VALUES (:id, :job_id, :attempt_no, :started_at, :heartbeat_at, :started_at, :started_at)
                    """
                ),
                {
                    "id": attempt_id,
                    "job_id": job_id,
                    "attempt_no": attempt_no,
                    "started_at": created_at.isoformat(),
                    "heartbeat_at": expired_at.isoformat(),
                },
            )
    finally:
        engine.dispose()
    return job_kind


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
