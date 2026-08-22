from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import ArtifactKind, ArtifactStatus, ExportKind, ExportStatus, JobStatus, new_id
from app.db.models import Artifact, Export
from app.modules.artifacts.cache import ArtifactCache
from app.modules.artifacts.store import ArtifactStore, ArtifactWrite


class RecoverableRunner(Protocol):
    def recover_expired(self, now: datetime) -> list[str]: ...

    def get(self, job_id: str): ...


@dataclass(frozen=True)
class SegmentCheckpoint:
    segment_id: str
    artifact_id: str
    provider_sent: bool
    usage_committed: bool
    was_cache_hit: bool = False


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
        cached = ArtifactCache(self.session, self.artifact_root).lookup(kind, input_hash, settings_hash)
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
        with ArtifactStore(self.artifact_root).begin(write) as writer:
            writer.file.write(payload)
            stored = writer.commit()

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
        return export


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
