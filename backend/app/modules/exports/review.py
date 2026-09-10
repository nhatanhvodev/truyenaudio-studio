from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import ExportKind, ExportStatus
from app.db.models import Artifact, Chapter, Export
from app.modules.compliance.rights import RightsGate
from app.modules.exports.schemas import GateDecision, PublicationMetadata


GATE_BLOCKED = "GATE_BLOCKED"
RIGHTS_CHANGED = "RIGHTS_CHANGED"
MASTER_CHANGED = "MASTER_CHANGED"
TRANSLATION_CHANGED = "TRANSLATION_CHANGED"
UNKNOWN_RIGHTS_PROVENANCE = "UNKNOWN_RIGHTS_PROVENANCE"
UNKNOWN_MASTER_PROVENANCE = "UNKNOWN_MASTER_PROVENANCE"
UNKNOWN_TRANSLATION_PROVENANCE = "UNKNOWN_TRANSLATION_PROVENANCE"
UNKNOWN_METADATA_PROVENANCE = "UNKNOWN_METADATA_PROVENANCE"
UNREADABLE_BUNDLE = "UNREADABLE_BUNDLE"

CHECKSUMS_FILE = "checksums.sha256"
REPORT_FILE = "production-report.json"
METADATA_FILE = "metadata.json"


@dataclass(frozen=True)
class ExportBundleStatus:
    """One READY export of a chapter, re-verified and re-checked for staleness."""

    id: str
    kind: str
    status: str
    manifest_sha256: str
    artifact_id: str
    directory_path: str
    files: tuple[str, ...]
    created_at: str
    verified: bool
    mismatches: tuple[str, ...]
    stale: bool
    stale_reasons: tuple[str, ...]


@dataclass(frozen=True)
class ChapterExportStatus:
    chapter_id: str
    gate: GateDecision
    bundles: tuple[ExportBundleStatus, ...]


@dataclass(frozen=True)
class _Inspection:
    readable: bool
    files: tuple[str, ...]
    verified: bool
    mismatches: tuple[str, ...]
    report: dict[str, object] | None = field(default=None)
    metadata: dict[str, object] | None = field(default=None)


class ExportReview:
    """Read-only review of what a chapter already exported (E01 status slice).

    Never writes, never uploads and never touches the network: it reads the
    export rows, re-verifies the delivered zip against the checksum manifest
    *inside* that zip, and compares the recorded provenance with the current
    upstream state so an edited chapter can no longer look fresh.
    """

    def __init__(
        self,
        session: Session,
        *,
        artifact_root: Path | str = Path("data") / "artifacts",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.artifact_root = Path(artifact_root).resolve()
        self._now = now or (lambda: datetime.now(UTC))

    def status(self, chapter_id: str) -> ChapterExportStatus:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError("CHAPTER_NOT_FOUND")

        gate = self._gate(chapter.id, ExportKind.PUBLICATION_BUNDLE)
        bundles = [
            self._bundle_status(chapter, export)
            for export in (
                self._latest_ready_export(chapter.id, kind) for kind in ExportKind
            )
            if export is not None
        ]
        bundles.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return ChapterExportStatus(
            chapter_id=chapter.id,
            gate=gate,
            bundles=tuple(bundles),
        )

    def _latest_ready_export(self, chapter_id: str, kind: ExportKind) -> Export | None:
        return self.session.scalars(
            select(Export)
            .where(
                Export.chapter_id == chapter_id,
                Export.kind == kind.value,
                Export.status == ExportStatus.READY.value,
                Export.bundle_artifact_id.is_not(None),
            )
            .order_by(Export.created_at.desc(), Export.id.desc())
        ).first()

    def _bundle_status(self, chapter: Chapter, export: Export) -> ExportBundleStatus:
        artifact = (
            self.session.get(Artifact, export.bundle_artifact_id)
            if export.bundle_artifact_id
            else None
        )
        inspection = self._inspect(artifact)
        stale_reasons = self._stale_reasons(chapter, export, inspection)
        return ExportBundleStatus(
            id=export.id,
            kind=export.kind,
            status=export.status,
            manifest_sha256=export.manifest_sha256 or "",
            artifact_id=export.bundle_artifact_id or "",
            directory_path=str(self._artifact_path(f"exports/builds/{export.id}")),
            files=inspection.files,
            created_at=_iso(export.created_at),
            verified=inspection.verified,
            mismatches=inspection.mismatches,
            stale=bool(stale_reasons),
            stale_reasons=tuple(stale_reasons),
        )

    def _inspect(self, artifact: Artifact | None) -> _Inspection:
        zip_path = None
        if artifact is not None:
            try:
                zip_path = self._artifact_path(artifact.relative_path)
            except ValueError:
                zip_path = None
        if zip_path is None or not zip_path.is_file():
            return _Inspection(
                readable=False,
                files=(),
                verified=False,
                mismatches=_recorded_files(artifact),
            )
        try:
            with zipfile.ZipFile(zip_path) as bundle:
                files = tuple(
                    sorted(
                        name for name in bundle.namelist() if not name.endswith("/")
                    )
                )
                mismatches = _mismatches(bundle, files)
                return _Inspection(
                    readable=True,
                    files=files,
                    verified=not mismatches,
                    mismatches=mismatches,
                    report=_read_json_entry(bundle, REPORT_FILE),
                    metadata=_read_json_entry(bundle, METADATA_FILE),
                )
        except (OSError, zipfile.BadZipFile, ValueError):
            return _Inspection(
                readable=False,
                files=(),
                verified=False,
                mismatches=_recorded_files(artifact),
            )

    def _stale_reasons(
        self, chapter: Chapter, export: Export, inspection: _Inspection
    ) -> list[str]:
        kind = ExportKind(export.kind)
        stored = (
            export.rights_evaluation_json
            if isinstance(export.rights_evaluation_json, dict)
            else {}
        )
        territory = stored.get("territory")
        metadata = _publication_metadata(inspection.metadata)
        unknown_metadata = (
            kind is ExportKind.PUBLICATION_BUNDLE
            and inspection.readable
            and metadata is None
        )

        gate = self._gate(
            chapter.id,
            kind,
            territory=territory if isinstance(territory, str) and territory else "VN",
            metadata=metadata,
        )

        reasons: list[str] = []
        if not gate.allowed:
            reasons.append(GATE_BLOCKED)

        stored_rights_hash = stored.get("rights_evaluation_hash")
        if not isinstance(stored_rights_hash, str) or not stored_rights_hash:
            reasons.append(UNKNOWN_RIGHTS_PROVENANCE)
        elif stored_rights_hash != gate.rights_evaluation_hash:
            reasons.append(RIGHTS_CHANGED)

        report = inspection.report if isinstance(inspection.report, dict) else None
        reasons.extend(
            _changed(
                _text_field(report, "master_artifact_id"),
                chapter.approved_master_artifact_id,
                changed=MASTER_CHANGED,
                unknown=UNKNOWN_MASTER_PROVENANCE,
            )
        )
        reasons.extend(
            _changed(
                _text_field(report, "translation_run_id"),
                chapter.approved_translation_run_id,
                changed=TRANSLATION_CHANGED,
                unknown=UNKNOWN_TRANSLATION_PROVENANCE,
            )
        )
        if unknown_metadata:
            reasons.append(UNKNOWN_METADATA_PROVENANCE)
        return reasons

    def _gate(
        self,
        chapter_id: str,
        kind: ExportKind,
        *,
        territory: str = "VN",
        metadata: PublicationMetadata | None = None,
    ) -> GateDecision:
        return (
            RightsGate(self.session, now=self._now)
            .evaluate(
                chapter_id,
                kind,
                territory=territory,
                metadata=metadata,
            )
            .decision
        )

    def _artifact_path(self, relative_path: str) -> Path:
        candidate = (self.artifact_root / relative_path).resolve()
        if not candidate.is_relative_to(self.artifact_root):
            raise ValueError("UNSAFE_ARTIFACT_PATH")
        return candidate


def _mismatches(bundle: zipfile.ZipFile, files: tuple[str, ...]) -> tuple[str, ...]:
    if CHECKSUMS_FILE not in files:
        return (CHECKSUMS_FILE,)
    expected = _parse_checksums(
        bundle.read(CHECKSUMS_FILE).decode("utf-8", errors="replace")
    )
    if not expected:
        return (CHECKSUMS_FILE,)
    names = set(files)
    broken = {name for name in expected if name not in names}
    # checksums.sha256 never lists itself; every other extra file is uncovered.
    broken.update(
        name for name in names if name != CHECKSUMS_FILE and name not in expected
    )
    for filename, digest in expected.items():
        if filename in broken:
            continue
        if _sha256_bytes(bundle.read(filename)) != digest:
            broken.add(filename)
    return tuple(sorted(broken))


def _parse_checksums(value: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in value.splitlines():
        if not line.strip():
            continue
        digest, _separator, filename = line.partition("  ")
        if not filename:
            continue
        checksums[filename] = digest
    return checksums


def _read_json_entry(bundle: zipfile.ZipFile, name: str) -> dict[str, object] | None:
    try:
        payload = json.loads(bundle.read(name).decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _recorded_files(artifact: Artifact | None) -> tuple[str, ...]:
    metadata = artifact.metadata_json if artifact is not None else None
    if not isinstance(metadata, dict):
        return (UNREADABLE_BUNDLE,)
    files = metadata.get("files")
    if not isinstance(files, list) or not files:
        return (UNREADABLE_BUNDLE,)
    return tuple(sorted(str(name) for name in files))


def _publication_metadata(payload: dict[str, object] | None) -> PublicationMetadata | None:
    if not isinstance(payload, dict):
        return None
    title = payload.get("episode_title")
    number = payload.get("suggested_episode_number")
    premium = payload.get("is_premium")
    if not isinstance(title, str) or type(number) is not int or type(premium) is not bool:
        return None
    try:
        return PublicationMetadata(title, number, premium)
    except ValueError:
        return None


def _text_field(payload: dict[str, object] | None, name: str) -> str | None:
    value = payload.get(name) if isinstance(payload, dict) else None
    return value if isinstance(value, str) and value else None


def _changed(
    stored: str | None, current: str | None, *, changed: str, unknown: str
) -> list[str]:
    if stored is None:
        return [unknown]
    return [] if stored == current else [changed]


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return ""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
