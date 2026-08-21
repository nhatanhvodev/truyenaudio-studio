from __future__ import annotations

from collections.abc import Callable
import asyncio
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ExportKind,
    ExportStatus,
    MasterResult,
    RunStatus,
    new_id,
)
from app.db.models import (
    Artifact,
    Chapter,
    Export,
    Project,
    SourceRevision,
    TranslationRun,
    TranslationSegment,
    VoicePlan,
    VoicePreset,
)
from app.modules.compliance.rights import RightsGate
from app.modules.exports.schemas import (
    EXPORT_SCHEMA_VERSION,
    ExportBundle,
    GateDecision,
    PublicationMetadata,
)
from app.providers.ffmpeg_audio import FFmpegAudioProcessor


PUBLICATION_FILES = (
    "tap-0001.mp3",
    "metadata.json",
    "ban-dich.md",
    "transcript.srt",
    "production-report.json",
    "provenance.json",
    "THIRD_PARTY_LICENSES.txt",
    "checksums.sha256",
)


class BundleVerificationError(ValueError):
    """Raised when an export bundle cannot be verified before READY."""


class ExportWorkflow:
    def __init__(
        self,
        session: Session,
        *,
        artifact_root: Path | str = Path("data") / "artifacts",
        audio_processor: object | None = None,
        id_factory: Callable[[], str] = new_id,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.artifact_root = Path(artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.audio_processor = audio_processor or FFmpegAudioProcessor()
        self.id_factory = id_factory
        self.now = now or (lambda: datetime.now(UTC))

    def evaluate_gate(
        self,
        chapter_id: str,
        kind: ExportKind,
        *,
        territory: str = "VN",
        metadata: PublicationMetadata | None = None,
        include_download: bool = False,
    ) -> GateDecision:
        return (
            RightsGate(self.session, now=self.now)
            .evaluate(
                chapter_id,
                ExportKind(kind),
                territory=territory,
                metadata=metadata,
                include_download=include_download,
            )
            .decision
        )

    def build_private_archive(self, chapter_id: str) -> ExportBundle:
        context = self._context(chapter_id, require_srt=False)
        evaluation = RightsGate(self.session, now=self.now).evaluate(
            chapter_id, ExportKind.PRIVATE_ARCHIVE
        )
        export_id = self.id_factory()
        files = {
            "PRIVATE_ONLY.txt": (
                "PRIVATE ONLY\n"
                "This archive is for local review and preservation only. It is not a publication bundle.\n"
            ).encode("utf-8"),
            "ban-dich.md": self._translation_markdown(context).encode("utf-8"),
            "source.txt": context.revision.normalized_text.encode("utf-8"),
            "production-report.json": _json_bytes(
                self._production_report(context, private=True)
            ),
            "provenance.json": _json_bytes(
                self._provenance(context, evaluation.payload)
            ),
        }
        return self._write_export(
            context,
            export_id,
            ExportKind.PRIVATE_ARCHIVE,
            files,
            evaluation.payload,
        )

    def build_publication_bundle(
        self,
        chapter_id: str,
        metadata: PublicationMetadata,
        *,
        territory: str = "VN",
        include_download: bool = False,
    ) -> ExportBundle:
        context = self._context(chapter_id, require_srt=True)
        evaluation = RightsGate(self.session, now=self.now).evaluate(
            chapter_id,
            ExportKind.PUBLICATION_BUNDLE,
            territory=territory,
            metadata=metadata,
            include_download=include_download,
        )
        if not evaluation.decision.allowed:
            raise PermissionError(",".join(evaluation.decision.reasons))
        master_path = self._artifact_path(context.master.relative_path)
        self._verify_master(context.master, master_path)
        files = {
            "tap-0001.mp3": master_path.read_bytes(),
            "metadata.json": _json_bytes(self._metadata(context, metadata)),
            "ban-dich.md": self._translation_markdown(context).encode("utf-8"),
            "transcript.srt": self._artifact_path(
                context.srt.relative_path
            ).read_bytes(),
            "production-report.json": _json_bytes(
                self._production_report(context, private=False)
            ),
            "provenance.json": _json_bytes(
                self._provenance(context, evaluation.payload)
            ),
            "THIRD_PARTY_LICENSES.txt": self._third_party_licenses(context).encode(
                "utf-8"
            ),
        }
        return self._write_export(
            context,
            self.id_factory(),
            ExportKind.PUBLICATION_BUNDLE,
            files,
            evaluation.payload,
            required_files=PUBLICATION_FILES,
        )

    def verify_bundle(self, directory_path: Path | str) -> dict[str, str]:
        root = Path(directory_path).resolve()
        checksum_path = root / "checksums.sha256"
        if not checksum_path.is_file():
            raise BundleVerificationError("CHECKSUMS_MISSING")
        checksums = _read_checksums(checksum_path.read_text(encoding="utf-8"))
        for filename, expected_sha256 in checksums.items():
            candidate = (root / filename).resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file():
                raise BundleVerificationError(f"CHECKSUM_FILE_MISSING:{filename}")
            actual_sha256 = _sha256_file(candidate)
            if actual_sha256 != expected_sha256:
                raise BundleVerificationError("CHECKSUM_MISMATCH")
        return checksums

    def _write_export(
        self,
        context: _ExportContext,
        export_id: str,
        kind: ExportKind,
        files: dict[str, bytes],
        rights_evaluation: dict[str, object],
        *,
        required_files: tuple[str, ...] | None = None,
    ) -> ExportBundle:
        if required_files is not None and set(files) != set(required_files) - {
            "checksums.sha256"
        }:
            raise BundleVerificationError("PUBLICATION_FILE_SET_MISMATCH")
        build_parent = self._artifact_path("exports/builds")
        build_parent.mkdir(parents=True, exist_ok=True)
        directory_path = build_parent / export_id
        tmp_path = build_parent / f".{export_id}.partial"
        if tmp_path.exists() or directory_path.exists():
            raise FileExistsError(export_id)
        tmp_path.mkdir()
        try:
            for filename, payload in files.items():
                _write_file(tmp_path, filename, payload)
            checksums = _checksums(tmp_path)
            _write_file(
                tmp_path,
                "checksums.sha256",
                "".join(
                    f"{digest}  {filename}\n"
                    for filename, digest in sorted(checksums.items())
                ).encode("utf-8"),
            )
            verified = self.verify_bundle(tmp_path)
            if set(verified) != set(files):
                raise BundleVerificationError("CHECKSUM_MANIFEST_MISMATCH")
            os.replace(tmp_path, directory_path)
        except Exception:
            shutil.rmtree(tmp_path, ignore_errors=True)
            raise

        zip_relative_path = f"exports/{context.chapter.id}/{export_id}.zip"
        zip_path = self._artifact_path(zip_relative_path)
        _zip_directory(directory_path, zip_path)
        zip_sha256 = _sha256_file(zip_path)
        manifest_sha256 = _sha256_file(directory_path / "checksums.sha256")
        artifact = Artifact(
            id=self.id_factory(),
            chapter_id=context.chapter.id,
            kind=ArtifactKind[kind.value].value,
            status=ArtifactStatus.READY.value,
            relative_path=zip_relative_path,
            sha256=zip_sha256,
            byte_size=zip_path.stat().st_size,
            mime_type="application/zip",
            input_hash=manifest_sha256,
            settings_hash=_canonical_sha256(
                {"kind": kind.value, "schema": EXPORT_SCHEMA_VERSION}
            ),
            producer="truyenaudio-studio",
            producer_version=EXPORT_SCHEMA_VERSION,
            metadata_json={"files": sorted((*files.keys(), "checksums.sha256"))},
        )
        self.session.add(artifact)
        self.session.flush()
        export = Export(
            id=export_id,
            chapter_id=context.chapter.id,
            kind=kind.value,
            status=ExportStatus.READY.value,
            bundle_artifact_id=artifact.id,
            manifest_sha256=manifest_sha256,
            rights_evaluation_json=rights_evaluation,
        )
        self.session.add(export)
        self.session.flush()
        context.chapter.last_export_id = export.id
        self.session.commit()
        return ExportBundle(
            id=export.id,
            chapter_id=context.chapter.id,
            kind=kind.value,
            status=export.status,
            files=tuple(sorted((*files.keys(), "checksums.sha256"))),
            directory_path=directory_path,
            zip_path=zip_path,
            artifact_id=artifact.id,
            manifest_sha256=manifest_sha256,
        )

    def _context(self, chapter_id: str, *, require_srt: bool) -> _ExportContext:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError("CHAPTER_NOT_FOUND")
        project = self.session.get(Project, chapter.project_id)
        if project is None:
            raise ValueError("PROJECT_NOT_FOUND")
        if chapter.approved_translation_run_id is None:
            raise ValueError("APPROVED_TRANSLATION_REQUIRED")
        run = self.session.get(TranslationRun, chapter.approved_translation_run_id)
        if run is None or run.status != RunStatus.APPROVED.value:
            raise ValueError("APPROVED_TRANSLATION_REQUIRED")
        if chapter.active_source_revision_id is None:
            raise ValueError("SOURCE_REVISION_REQUIRED")
        revision = self.session.get(SourceRevision, chapter.active_source_revision_id)
        if revision is None:
            raise ValueError("SOURCE_REVISION_REQUIRED")
        if chapter.approved_master_artifact_id is None:
            raise ValueError("APPROVED_MASTER_REQUIRED")
        master = self.session.get(Artifact, chapter.approved_master_artifact_id)
        if (
            master is None
            or master.kind != ArtifactKind.MASTER_MP3.value
            or master.status != ArtifactStatus.READY.value
        ):
            raise ValueError("APPROVED_MASTER_REQUIRED")
        master_metadata = master.metadata_json or {}
        if master_metadata.get("translation_run_id") != run.id:
            raise ValueError("MASTER_TRANSLATION_STALE")
        if master_metadata.get("voice_plan_id") != chapter.active_voice_plan_id:
            raise ValueError("MASTER_VOICE_PLAN_STALE")
        srt = self.session.scalar(
            select(Artifact)
            .where(
                Artifact.chapter_id == chapter.id,
                Artifact.kind == ArtifactKind.SRT.value,
                Artifact.status == ArtifactStatus.READY.value,
            )
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        )
        if srt is None and require_srt:
            raise ValueError("SRT_REQUIRED")
        return _ExportContext(
            project=project,
            chapter=chapter,
            revision=revision,
            run=run,
            master=master,
            srt=srt,
        )

    def _verify_master(self, artifact: Artifact, path: Path) -> MasterResult | None:
        if not path.is_file() or _sha256_file(path) != artifact.sha256:
            raise BundleVerificationError("MASTER_CHECKSUM_MISMATCH")
        if artifact.mime_type != "audio/mpeg" or artifact.duration_ms is None:
            raise BundleVerificationError("MASTER_METADATA_INVALID")
        if self.audio_processor is None:
            return None
        probe = asyncio.run(self.audio_processor.probe(path, artifact.sha256))
        if probe.sha256 != artifact.sha256 or probe.codec.lower() != "mp3":
            raise BundleVerificationError("MASTER_PROBE_MISMATCH")
        if (
            probe.duration_ms <= 0
            or abs(probe.duration_ms - artifact.duration_ms) > 1000
        ):
            raise BundleVerificationError("MASTER_PROBE_MISMATCH")
        return probe

    def _metadata(
        self, context: _ExportContext, metadata: PublicationMetadata
    ) -> dict[str, object]:
        return {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "episode_title": metadata.episode_title,
            "suggested_episode_number": metadata.suggested_episode_number,
            "is_premium": metadata.is_premium,
            "audio_file": "tap-0001.mp3",
            "duration_seconds": round((context.master.duration_ms or 0) / 1000),
            "language": context.project.target_language,
        }

    def _translation_markdown(self, context: _ExportContext) -> str:
        lines = [
            f"# {context.chapter.translated_title or context.chapter.source_title or 'Chapter'}",
            "",
        ]
        for segment in self._translation_segments(context.run.id):
            lines.extend((segment.target_text, ""))
        return "\n".join(lines).rstrip() + "\n"

    def _production_report(
        self, context: _ExportContext, *, private: bool
    ) -> dict[str, object]:
        return {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "private_archive": private,
            "chapter_id": context.chapter.id,
            "translation_run_id": context.run.id,
            "translation_sha256": context.run.translation_text_sha256,
            "prompt_version": context.run.prompt_version,
            "master_artifact_id": context.master.id,
            "master_sha256": context.master.sha256,
            "duration_seconds": round((context.master.duration_ms or 0) / 1000),
            "generated_at": self.now().astimezone(UTC).isoformat(),
        }

    def _provenance(
        self, context: _ExportContext, rights_evaluation: dict[str, object]
    ) -> dict[str, object]:
        return {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "source": {
                "reference": context.project.source_reference_url,
                "type": context.project.source_type,
            },
            "rights": {
                "allowed": rights_evaluation.get("allowed", False),
                "status": rights_evaluation.get("rights_status"),
                "evaluation_hash": rights_evaluation.get("rights_evaluation_hash"),
                "required_scopes": rights_evaluation.get("required_scopes", []),
            },
            "evidence": rights_evaluation.get("evidence", []),
        }

    def _third_party_licenses(self, context: _ExportContext) -> str:
        plan = (
            self.session.get(VoicePlan, context.chapter.active_voice_plan_id)
            if context.chapter.active_voice_plan_id
            else None
        )
        preset = (
            self.session.get(VoicePreset, plan.narrator_preset_id)
            if plan is not None
            else None
        )
        if preset is None or preset.license_snapshot_artifact_id is None:
            return "No third-party license snapshot declared for this export.\n"
        artifact = self.session.get(Artifact, preset.license_snapshot_artifact_id)
        if artifact is None or artifact.status != ArtifactStatus.READY.value:
            return "Third-party license snapshot artifact is unavailable.\n"
        return self._artifact_path(artifact.relative_path).read_text(encoding="utf-8")

    def _translation_segments(self, run_id: str) -> tuple[TranslationSegment, ...]:
        return tuple(
            self.session.scalars(
                select(TranslationSegment)
                .where(TranslationSegment.translation_run_id == run_id)
                .order_by(TranslationSegment.created_at, TranslationSegment.id)
            )
        )

    def _artifact_path(self, relative_path: str) -> Path:
        candidate = (self.artifact_root / relative_path).resolve()
        if not candidate.is_relative_to(self.artifact_root):
            raise ValueError("UNSAFE_ARTIFACT_PATH")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate


class _ExportContext:
    def __init__(
        self,
        *,
        project: Project,
        chapter: Chapter,
        revision: SourceRevision,
        run: TranslationRun,
        master: Artifact,
        srt: Artifact | None,
    ) -> None:
        self.project = project
        self.chapter = chapter
        self.revision = revision
        self.run = run
        self.master = master
        self.srt = srt


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _write_file(root: Path, filename: str, payload: bytes) -> None:
    path = (root / filename).resolve()
    if not path.is_relative_to(root) or path.name != filename:
        raise ValueError("UNSAFE_EXPORT_FILE")
    path.write_bytes(payload)


def _checksums(root: Path) -> dict[str, str]:
    return {
        path.name: _sha256_file(path)
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.name != "checksums.sha256"
    }


def _read_checksums(value: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in value.splitlines():
        if not line.strip():
            continue
        digest, filename = line.split("  ", maxsplit=1)
        checksums[filename] = digest
    return checksums


def _zip_directory(directory_path: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_zip = zip_path.with_name(f".{zip_path.name}.partial")
    with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(directory_path.iterdir(), key=lambda item: item.name):
            if path.is_file():
                bundle.write(path, arcname=path.name)
    os.replace(tmp_zip, zip_path)


def _sha256_file(path: Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            sha256.update(chunk)
    return sha256.hexdigest()


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
