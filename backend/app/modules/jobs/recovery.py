from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import ArtifactKind, ArtifactStatus, ExportKind, ExportStatus, JobStatus, new_id
from app.db.models import Artifact, Export
from app.modules.artifacts.cache import ArtifactCache
from app.modules.artifacts.store import ArtifactStore, ArtifactWrite
from app.modules.jobs.runner import JobLease


class RecoverableRunner(Protocol):
    def recover_expired(self, now: datetime) -> list[str]: ...

    def get(self, job_id: str): ...

    def mark_provider_sent(
        self,
        job_id: str,
        worker_id: str,
        attempt_id: str,
        provider_request_id: str,
    ) -> None: ...


@dataclass(frozen=True)
class SegmentCheckpoint:
    segment_id: str
    artifact_id: str
    provider_sent: bool
    usage_committed: bool
    was_cache_hit: bool = False


@dataclass(frozen=True)
class ArtifactPayload:
    payload: bytes
    provider_request_id: str | None = None
    metadata: dict[str, object] | None = None
    duration_ms: int | None = None
    expected_sha256: str | None = None


@dataclass(frozen=True)
class RecoverySummary:
    job_ids: tuple[str, ...]
    requeued: int
    canceled: int
    billing_unknown: int
    failed: int


class RecoveryArtifactWriter:
    def __init__(
        self,
        session: Session,
        artifact_root: Path | str,
        *,
        id_factory=new_id,
    ) -> None:
        self.session = session
        self.artifact_root = Path(artifact_root).resolve()
        self.id_factory = id_factory

    def lookup_ready_artifact(
        self,
        kind: ArtifactKind,
        input_hash: str,
        settings_hash: str,
    ) -> Artifact | None:
        return ArtifactCache(self.session, self.artifact_root).lookup(kind, input_hash, settings_hash)

    def checkpoint_ready_artifact(
        self,
        *,
        chapter_id: str,
        kind: ArtifactKind,
        segment_id: str,
        input_hash: str,
        settings_hash: str,
        payload: bytes,
        mime_type: str,
        metadata: dict[str, object] | None = None,
        provider_sent: bool = False,
        usage_committed: bool = True,
    ) -> SegmentCheckpoint:
        cached = self.lookup_ready_artifact(kind, input_hash, settings_hash)
        if cached is not None:
            return SegmentCheckpoint(
                segment_id=segment_id,
                artifact_id=cached.id,
                provider_sent=provider_sent,
                usage_committed=usage_committed,
                was_cache_hit=True,
            )

        artifact_id = self.id_factory()
        relative_path = _artifact_relative_path(chapter_id, kind, artifact_id, mime_type)
        write = ArtifactWrite(
            kind=kind,
            relative_path=relative_path,
            input_hash=input_hash,
            settings_hash=settings_hash,
            mime_type=mime_type,
        )
        stored = self.write_payload(write, payload)

        artifact = Artifact(
            id=artifact_id,
            chapter_id=chapter_id,
            kind=kind.value,
            status=ArtifactStatus.READY.value,
            relative_path=stored.relative_path,
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            mime_type=mime_type,
            producer="truyenaudio-studio",
            producer_version="recovery-v1",
            input_hash=input_hash,
            settings_hash=settings_hash,
            metadata_json=metadata or {},
        )
        self.session.add(artifact)
        self.session.flush()
        return SegmentCheckpoint(
            segment_id=segment_id,
            artifact_id=artifact.id,
            provider_sent=provider_sent,
            usage_committed=usage_committed,
        )

    def write_payload(self, write: ArtifactWrite, payload: bytes) -> object:
        with ArtifactStore(self.artifact_root).begin(write) as writer:
            writer.file.write(payload)
            return writer.commit()

    def add_ready_artifact(
        self,
        *,
        artifact_id: str,
        chapter_id: str,
        kind: ArtifactKind,
        relative_path: str,
        sha256: str,
        byte_size: int,
        mime_type: str,
        duration_ms: int | None,
        input_hash: str,
        settings_hash: str,
        metadata: dict[str, object] | None,
    ) -> Artifact:
        artifact = Artifact(
            id=artifact_id,
            chapter_id=chapter_id,
            kind=kind.value,
            status=ArtifactStatus.READY.value,
            relative_path=relative_path,
            sha256=sha256,
            byte_size=byte_size,
            mime_type=mime_type,
            duration_ms=duration_ms,
            producer="truyenaudio-studio",
            producer_version="recovery-v1",
            input_hash=input_hash,
            settings_hash=settings_hash,
            metadata_json=metadata or {},
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact

    def checkpoint_ready_export(
        self,
        *,
        chapter_id: str,
        kind: ExportKind,
        manifest_sha256: str,
        bundle_artifact_id: str,
        rights_evaluation: dict[str, object] | None = None,
    ) -> Export:
        existing = self.session.scalar(
            select(Export)
            .where(
                Export.chapter_id == chapter_id,
                Export.kind == kind.value,
                Export.status == ExportStatus.READY.value,
                Export.manifest_sha256 == manifest_sha256,
            )
            .order_by(Export.created_at.desc(), Export.id.desc())
        )
        if existing is not None:
            self._coalesce_ready_exports(existing)
            return existing

        export = Export(
            id=self.id_factory(),
            chapter_id=chapter_id,
            kind=kind.value,
            status=ExportStatus.READY.value,
            bundle_artifact_id=bundle_artifact_id,
            manifest_sha256=manifest_sha256,
            rights_evaluation_json=rights_evaluation or {},
        )
        self.session.add(export)
        self.session.flush()
        self._coalesce_ready_exports(export)
        return export

    def _coalesce_ready_exports(self, keeper: Export) -> None:
        duplicates = self.session.scalars(
            select(Export).where(
                Export.chapter_id == keeper.chapter_id,
                Export.kind == keeper.kind,
                Export.status == ExportStatus.READY.value,
                Export.manifest_sha256 == keeper.manifest_sha256,
                Export.id != keeper.id,
            )
        ).all()
        for duplicate in duplicates:
            duplicate.status = ExportStatus.REVOKED.value
        if duplicates:
            self.session.flush()


class RecoveryJobContext:
    def __init__(
        self,
        *,
        runner: RecoverableRunner,
        lease: JobLease,
        session: Session,
        artifact_root: Path | str,
        id_factory=new_id,
    ) -> None:
        self.runner = runner
        self.lease = lease
        self.session = session
        self.artifacts = RecoveryArtifactWriter(session, artifact_root, id_factory=id_factory)
        self.id_factory = id_factory

    def checkpoint_segment_artifact(
        self,
        *,
        chapter_id: str,
        kind: ArtifactKind,
        segment_id: str,
        input_hash: str,
        settings_hash: str,
        mime_type: str,
        provider: Callable[[], ArtifactPayload],
        authorize: Callable[[], None] | None = None,
        commit_usage: Callable[[ArtifactPayload], None] | None = None,
        provider_request_id: str | None = None,
    ) -> SegmentCheckpoint:
        self.raise_if_cancel_requested()
        cached = self.artifacts.lookup_ready_artifact(kind, input_hash, settings_hash)
        if cached is not None:
            return SegmentCheckpoint(
                segment_id=segment_id,
                artifact_id=cached.id,
                provider_sent=False,
                usage_committed=True,
                was_cache_hit=True,
            )

        if authorize is not None:
            authorize()
        self.session.commit()

        if provider_request_id is not None:
            self.mark_provider_sent(provider_request_id)
        result = provider()
        sent_request_id = provider_request_id or result.provider_request_id
        if sent_request_id is not None:
            self.mark_provider_sent(sent_request_id)

        artifact_id = self.id_factory()
        relative_path = _artifact_relative_path(chapter_id, kind, artifact_id, mime_type)
        write = ArtifactWrite(
            kind=kind,
            relative_path=relative_path,
            input_hash=input_hash,
            settings_hash=settings_hash,
            mime_type=mime_type,
        )
        with ArtifactStore(self.artifacts.artifact_root).begin(write) as writer:
            writer.file.write(result.payload)
            self.raise_if_cancel_requested()
            stored = writer.commit()
        if result.expected_sha256 is not None and stored.sha256 != result.expected_sha256:
            Path(self.artifacts.artifact_root, stored.relative_path).unlink(missing_ok=True)
            raise ValueError("ARTIFACT_CHECKSUM_MISMATCH")

        artifact = self.artifacts.add_ready_artifact(
            artifact_id=artifact_id,
            chapter_id=chapter_id,
            kind=kind,
            relative_path=stored.relative_path,
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            mime_type=mime_type,
            duration_ms=result.duration_ms,
            input_hash=input_hash,
            settings_hash=settings_hash,
            metadata=result.metadata,
        )
        if commit_usage is not None:
            commit_usage(result)
        return SegmentCheckpoint(
            segment_id=segment_id,
            artifact_id=artifact.id,
            provider_sent=sent_request_id is not None,
            usage_committed=True,
        )

    def cancel_requested(self) -> bool:
        return self.runner.get(self.lease.job_id).status is JobStatus.CANCEL_REQUESTED

    def raise_if_cancel_requested(self) -> None:
        if self.cancel_requested():
            raise RecoveryCanceled(self.lease.job_id)

    def mark_provider_sent(self, provider_request_id: str) -> None:
        self.runner.mark_provider_sent(
            self.lease.job_id,
            self.lease.worker_id,
            self.lease.attempt_id,
            provider_request_id,
        )

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()


class RecoveryCanceled(Exception):
    pass


def recover_expired(runner: RecoverableRunner, now: datetime) -> RecoverySummary:
    job_ids = tuple(runner.recover_expired(now))
    statuses = [runner.get(job_id).status for job_id in job_ids]
    return RecoverySummary(
        job_ids=job_ids,
        requeued=sum(status is JobStatus.QUEUED for status in statuses),
        canceled=sum(status is JobStatus.CANCELED for status in statuses),
        billing_unknown=sum(status is JobStatus.BILLING_UNKNOWN for status in statuses),
        failed=sum(status is JobStatus.FAILED for status in statuses),
    )


def _artifact_relative_path(chapter_id: str, kind: ArtifactKind, artifact_id: str, mime_type: str) -> str:
    return f"recovery/{chapter_id}/{kind.value.lower()}/{artifact_id}{_extension(mime_type)}"


def _extension(mime_type: str) -> str:
    return {
        "application/zip": ".zip",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "text/markdown; charset=utf-8": ".md",
    }.get(mime_type, ".bin")
