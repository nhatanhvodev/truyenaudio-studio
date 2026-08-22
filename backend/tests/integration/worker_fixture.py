from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path

from sqlalchemy import Engine, text

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ExportKind,
    ExportStatus,
    JobKind,
    JobStatus,
    RightsStatus,
    SourceType,
)
from app.db.base import session_factory
from app.db.models import Export
from app.modules.jobs.recovery import ArtifactPayload, RecoveryArtifactWriter, RecoveryJobContext
from app.modules.jobs.runner import JobLease, JobRunner
from app.worker import Worker


NOW = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)
PROJECT_ID = "018f0000-0000-7000-8000-910000000001"
CHAPTER_ID = "018f0000-0000-7000-8000-910000000002"


class SimulatedWorkerCrash(BaseException):
    pass


class ManualClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        current = self.value
        self.value = current + timedelta(seconds=1)
        return current


@dataclass(frozen=True)
class StageRun:
    job_id: str
    stage: JobKind
    runner: JobRunner
    clock: ManualClock
    artifact_root: Path
    provider_calls: dict[str, int]
    worker_driven_recovery_count: int = 0

    def kill_worker(self) -> None:
        self.clock.value = NOW + timedelta(seconds=203)

    async def restart_worker(self) -> None:
        worker = self._worker(kill_after=None)
        recovered = await worker.run_once()
        assert recovered is False
        object.__setattr__(self, "worker_driven_recovery_count", self.worker_driven_recovery_count + 1)
        next_run_at = self.runner.get(self.job_id).next_run_at
        if next_run_at is not None:
            self.clock.value = next_run_at
        worked = await worker.run_once()
        assert worked is True

    async def restart_worker_for_recovery_only(self) -> None:
        worker = self._worker(kill_after=None)
        worked = await worker.run_once()
        assert worked is False
        object.__setattr__(self, "worker_driven_recovery_count", self.worker_driven_recovery_count + 1)

    def wait_success(self) -> None:
        assert self.runner.get(self.job_id).status is JobStatus.SUCCEEDED

    def ready_artifact_hashes(self) -> set[str]:
        with self.runner.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT kind, input_hash, settings_hash, sha256
                    FROM artifacts
                    WHERE status = :ready
                    ORDER BY kind, input_hash, settings_hash, sha256
                    """
                ),
                {"ready": ArtifactStatus.READY.value},
            ).all()
        return {":".join(row) for row in rows}

    def duplicate_ready_cache_keys(self) -> list[tuple[str, str, str, int]]:
        with self.runner.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT kind, input_hash, settings_hash, COUNT(*) AS duplicate_count
                    FROM artifacts
                    WHERE status = :ready
                    GROUP BY kind, input_hash, settings_hash
                    HAVING COUNT(*) > 1
                    ORDER BY kind, input_hash, settings_hash
                    """
                ),
                {"ready": ArtifactStatus.READY.value},
            ).all()
        return [(row.kind, row.input_hash, row.settings_hash, row.duplicate_count) for row in rows]

    def duplicate_ready_export_manifests(self) -> list[tuple[str, str, int]]:
        with self.runner.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT kind, manifest_sha256, COUNT(*) AS duplicate_count
                    FROM exports
                    WHERE status = :ready
                    GROUP BY kind, manifest_sha256
                    HAVING COUNT(*) > 1
                    ORDER BY kind, manifest_sha256
                    """
                ),
                {"ready": ExportStatus.READY.value},
            ).all()
        return [(row.kind, row.manifest_sha256, row.duplicate_count) for row in rows]

    def missing_ready_artifacts(self) -> list[str]:
        with self.runner.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT relative_path
                    FROM artifacts
                    WHERE status = :ready
                    ORDER BY relative_path
                    """
                ),
                {"ready": ArtifactStatus.READY.value},
            ).all()
        return [
            row.relative_path
            for row in rows
            if not (self.artifact_root / row.relative_path).is_file()
        ]

    def partial_artifacts(self) -> list[str]:
        return sorted(str(path.relative_to(self.artifact_root)) for path in self.artifact_root.rglob("*.partial"))

    async def run_until_crash(self, kill_after: str) -> None:
        worker = self._worker(kill_after=kill_after)
        try:
            await worker.run_once()
        except SimulatedWorkerCrash:
            return
        raise AssertionError("worker did not crash")

    async def run_until_cancel_acknowledged(self) -> None:
        worker = self._worker(kill_after=None, cancel_after_partial_segment=2)
        worked = await worker.run_once()
        assert worked is True

    async def run_until_cloud_unclear_after_provider_sent(self) -> None:
        worker = self._worker(kill_after=None, cloud_unclear_segment=1)
        try:
            await worker.run_once()
        except SimulatedWorkerCrash:
            return
        raise AssertionError("worker did not crash after cloud provider send")

    def seed_duplicate_ready_exports(self) -> None:
        manifest_sha256 = _hash(f"{self.stage.value}:manifest")
        with session_factory(self.runner.engine)() as session:
            session.add_all(
                (
                    Export(
                        id="018f0000-0000-7000-8000-910000000901",
                        chapter_id=CHAPTER_ID,
                        kind=ExportKind.PUBLICATION_BUNDLE.value,
                        status=ExportStatus.READY.value,
                        bundle_artifact_id=None,
                        manifest_sha256=manifest_sha256,
                        rights_evaluation_json={"allowed": True},
                    ),
                    Export(
                        id="018f0000-0000-7000-8000-910000000902",
                        chapter_id=CHAPTER_ID,
                        kind=ExportKind.PUBLICATION_BUNDLE.value,
                        status=ExportStatus.READY.value,
                        bundle_artifact_id=None,
                        manifest_sha256=manifest_sha256,
                        rights_evaluation_json={"allowed": True},
                    ),
                )
            )
            session.commit()

    def coalesce_ready_export_checkpoint(self) -> None:
        with session_factory(self.runner.engine)() as session:
            writer = RecoveryArtifactWriter(session, self.artifact_root)
            writer.checkpoint_ready_export(
                chapter_id=CHAPTER_ID,
                kind=ExportKind.PUBLICATION_BUNDLE,
                manifest_sha256=_hash(f"{self.stage.value}:manifest"),
                bundle_artifact_id=None,
                rights_evaluation={"allowed": True},
            )
            session.commit()

    def _worker(
        self,
        *,
        kill_after: str | None,
        cancel_after_partial_segment: int | None = None,
        cloud_unclear_segment: int | None = None,
    ) -> Worker:
        return Worker(
            self.runner,
            handlers={
                self.stage: _stage_handler(
                    self.runner,
                    self.artifact_root,
                    self.stage,
                    self.provider_calls,
                    kill_after=kill_after,
                    cancel_after_partial_segment=cancel_after_partial_segment,
                    cloud_unclear_segment=cloud_unclear_segment,
                )
            },
            worker_id="worker-a",
            clock=self.clock,
            heartbeat_interval_seconds=0.01,
            artifact_root=self.artifact_root,
        )


class StudioProcessFixture:
    def __init__(self, engine: Engine, artifact_root: Path, id_factory) -> None:
        self.engine = engine
        self.artifact_root = artifact_root
        self.id_factory = id_factory
        self.runner = JobRunner(engine, id_factory=id_factory)
        self.clock = ManualClock(NOW)
        self.provider_calls: dict[str, int] = {}
        _insert_project(engine)

    def start_fake_chapter(self, *, stage: str) -> StageRun:
        job_kind = JobKind(stage)
        job = self.runner.enqueue(job_kind, PROJECT_ID, CHAPTER_ID, f"recovery:{stage}")
        return StageRun(
            job_id=job.id,
            stage=job_kind,
            runner=self.runner,
            clock=self.clock,
            artifact_root=self.artifact_root,
            provider_calls=self.provider_calls,
        )


def _stage_handler(
    runner: JobRunner,
    artifact_root: Path,
    stage: JobKind,
    provider_calls: dict[str, int],
    *,
    kill_after: str | None,
    cancel_after_partial_segment: int | None,
    cloud_unclear_segment: int | None,
):
    async def handle(lease: JobLease, recovery: RecoveryJobContext) -> str | None:
        artifact_id: str | None = None
        for segment_number in range(1, 4):
            segment_id = f"{stage.value}:segment:{segment_number}"

            def provider() -> ArtifactPayload:
                provider_calls[segment_id] = provider_calls.get(segment_id, 0) + 1
                if cloud_unclear_segment == segment_number:
                    raise SimulatedWorkerCrash(segment_id)
                if cancel_after_partial_segment == segment_number:
                    runner.request_cancel(lease.job_id, NOW + timedelta(seconds=segment_number))
                return ArtifactPayload(
                    payload=f"{stage.value} payload {segment_number}".encode("utf-8"),
                    provider_request_id=f"provider-{segment_id}" if cloud_unclear_segment == segment_number else None,
                    metadata={"stage": stage.value, "segment": segment_number},
                )

            checkpoint = recovery.checkpoint_segment_artifact(
                chapter_id=CHAPTER_ID,
                kind=_artifact_kind(stage),
                segment_id=segment_id,
                input_hash=_hash(f"{stage.value}:input:{segment_number}"),
                settings_hash=_hash(f"{stage.value}:settings"),
                mime_type=_mime_type(stage),
                provider=provider,
                provider_request_id=f"provider-{segment_id}" if cloud_unclear_segment == segment_number else None,
                cancel_after_temp_write=cancel_after_partial_segment == segment_number,
            )
            artifact_id = checkpoint.artifact_id
            recovery.commit()
            if kill_after == segment_id:
                raise SimulatedWorkerCrash(segment_id)
            if stage is JobKind.EXPORT and artifact_id is not None:
                export = recovery.artifacts.checkpoint_ready_export(
                    chapter_id=CHAPTER_ID,
                    kind=ExportKind.PUBLICATION_BUNDLE,
                    manifest_sha256=_hash(f"{stage.value}:manifest"),
                    bundle_artifact_id=artifact_id,
                    rights_evaluation={"allowed": True},
                )
                artifact_id = export.bundle_artifact_id
        recovery.commit()
        return artifact_id

    return handle


def _insert_project(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT OR IGNORE INTO projects
                (id, title, slug, source_type, rights_status, created_at, updated_at)
                VALUES (:id, 'Recovery Project', 'recovery-project', :source_type, :rights_status, :now, :now)
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
                INSERT OR IGNORE INTO chapters
                (id, project_id, ordinal, state, created_at, updated_at)
                VALUES (:id, :project_id, 1, 'IMPORTED', :now, :now)
                """
            ),
            {"id": CHAPTER_ID, "project_id": PROJECT_ID, "now": NOW.isoformat()},
        )


def _cancel_requested(runner: JobRunner, job_id: str) -> bool:
    return runner.get(job_id).status is JobStatus.CANCEL_REQUESTED


def _artifact_kind(stage: JobKind) -> ArtifactKind:
    return {
        JobKind.TRANSLATE: ArtifactKind.TRANSLATION_MARKDOWN,
        JobKind.SYNTHESIZE: ArtifactKind.TTS_SEGMENT,
        JobKind.MASTER: ArtifactKind.MASTER_MP3,
        JobKind.EXPORT: ArtifactKind.PUBLICATION_BUNDLE,
    }[stage]


def _mime_type(stage: JobKind) -> str:
    return {
        JobKind.TRANSLATE: "text/markdown; charset=utf-8",
        JobKind.SYNTHESIZE: "audio/wav",
        JobKind.MASTER: "audio/mpeg",
        JobKind.EXPORT: "application/zip",
    }[stage]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
