from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import hashlib
from pathlib import Path, PureWindowsPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import ArtifactKind, ArtifactStatus, EvidenceKind, RightsScope, new_id
from app.db.models import Artifact, Project, RightsEvidence, RightsGrant
from app.modules.artifacts.store import ArtifactAlreadyExists, ArtifactStore, ArtifactWrite


MAX_EVIDENCE_BYTES = 10 * 1024 * 1024
TXT_MIME = "text/plain; charset=utf-8"


@dataclass(frozen=True)
class EvidenceUpload:
    filename: str
    payload: bytes
    evidence_kind: EvidenceKind
    issuer: str | None
    expires_at: datetime | None
    issued_at: datetime | None = None
    notes: str | None = None


@dataclass(frozen=True)
class CreateGrant:
    scope: RightsScope
    territory: str
    allows_ai_processing: bool
    allows_third_party_cloud: bool
    valid_from: date | datetime
    expires_at: date | datetime | None
    evidence_id: str | None


@dataclass(frozen=True)
class RightsEvidenceView:
    id: str
    project_id: str
    artifact_id: str
    evidence_kind: EvidenceKind
    display_name: str
    relative_path: str
    sha256: str
    issuer: str | None
    issued_at: datetime | None
    expires_at: datetime | None


@dataclass(frozen=True)
class RightsGrantView:
    id: str
    project_id: str
    scope: RightsScope
    territory: str
    allows_ai_processing: bool
    allows_third_party_cloud: bool
    valid_from: datetime
    expires_at: datetime | None
    evidence_id: str | None


class EvidenceService:
    def __init__(self, session: Session, artifact_store: ArtifactStore) -> None:
        self.session = session
        self.artifact_store = artifact_store

    def store(self, project_id: str, command: EvidenceUpload) -> RightsEvidenceView:
        project = self.session.get(Project, project_id)
        if project is None:
            raise ValueError("PROJECT_NOT_FOUND")
        if len(command.payload) > MAX_EVIDENCE_BYTES:
            raise ValueError("RIGHTS_EVIDENCE_TOO_LARGE")
        display_name = _safe_basename(command.filename)
        mime_type = _detect_mime(display_name, command.payload)
        sha256 = hashlib.sha256(command.payload).hexdigest()
        evidence_id = new_id()
        relative_path = f"projects/{project.slug}/evidence/{evidence_id}-{display_name}"
        settings_hash = hashlib.sha256(
            f"{project.id}:{display_name}:{command.evidence_kind.name}".encode("utf-8")
        ).hexdigest()
        write = ArtifactWrite(
            kind=ArtifactKind.RIGHTS_EVIDENCE,
            relative_path=relative_path,
            input_hash=sha256,
            settings_hash=settings_hash,
            mime_type=mime_type,
        )
        with self.artifact_store.begin(write) as writer:
            writer.file.write(command.payload)
            try:
                stored = writer.commit()
            except ArtifactAlreadyExists as exc:
                raise ValueError("RIGHTS_EVIDENCE_ALREADY_EXISTS") from exc
        artifact = Artifact(
            id=new_id(),
            kind=ArtifactKind.RIGHTS_EVIDENCE.name,
            status=ArtifactStatus.READY.name,
            relative_path=stored.relative_path,
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            mime_type=mime_type,
            input_hash=write.input_hash,
            settings_hash=write.settings_hash,
            producer="EvidenceService",
            producer_version="task1",
        )
        evidence = RightsEvidence(
            id=evidence_id,
            project_id=project_id,
            evidence_kind=command.evidence_kind.name,
            display_name=display_name,
            relative_path=stored.relative_path,
            sha256=sha256,
            issuer=command.issuer,
            issued_at=command.issued_at,
            expires_at=command.expires_at,
            notes=command.notes,
        )
        self.session.add_all([artifact, evidence])
        self.session.commit()
        return _evidence_view(evidence, artifact.id)

    def list(self, project_id: str) -> tuple[RightsEvidenceView, ...]:
        rows = self.session.scalars(
            select(RightsEvidence).where(RightsEvidence.project_id == project_id).order_by(RightsEvidence.created_at)
        ).all()
        return tuple(_evidence_view(row, _artifact_id_for_evidence(self.session, row)) for row in rows)


class GrantService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, project_id: str, command: CreateGrant) -> RightsGrantView:
        if self.session.get(Project, project_id) is None:
            raise ValueError("PROJECT_NOT_FOUND")
        if type(command.allows_ai_processing) is not bool or type(command.allows_third_party_cloud) is not bool:
            raise ValueError("RIGHTS_BOOLEAN_REQUIRED")
        territory = command.territory.strip()
        if not territory:
            raise ValueError("RIGHTS_TERRITORY_REQUIRED")
        valid_from = _as_utc_datetime(command.valid_from)
        expires_at = _as_utc_datetime(command.expires_at) if command.expires_at is not None else None
        if expires_at is not None and valid_from > expires_at:
            raise ValueError("RIGHTS_DATE_RANGE_INVALID")
        if command.evidence_id is not None:
            evidence = self.session.get(RightsEvidence, command.evidence_id)
            if evidence is None or evidence.project_id != project_id:
                raise ValueError("RIGHTS_EVIDENCE_PROJECT_MISMATCH")
        existing = self.session.scalar(
            select(RightsGrant).where(
                RightsGrant.project_id == project_id,
                RightsGrant.scope == command.scope.name,
                RightsGrant.territory == territory,
                RightsGrant.valid_from == valid_from,
            )
        )
        if existing is not None:
            raise ValueError("RIGHTS_GRANT_DUPLICATE")
        grant = RightsGrant(
            id=new_id(),
            project_id=project_id,
            scope=command.scope.name,
            territory=territory,
            allows_ai_processing=command.allows_ai_processing,
            allows_third_party_cloud=command.allows_third_party_cloud,
            valid_from=valid_from,
            expires_at=expires_at,
            evidence_id=command.evidence_id,
        )
        self.session.add(grant)
        self.session.commit()
        return _grant_view(grant)

    def list(self, project_id: str) -> tuple[RightsGrantView, ...]:
        rows = self.session.scalars(
            select(RightsGrant).where(RightsGrant.project_id == project_id).order_by(RightsGrant.valid_from)
        ).all()
        return tuple(_grant_view(row) for row in rows)


def _safe_basename(filename: str) -> str:
    name = Path(PureWindowsPath(filename).name).name
    if not name or name in {".", ".."}:
        raise ValueError("RIGHTS_EVIDENCE_FILENAME_REQUIRED")
    return name


def _detect_mime(filename: str, payload: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf" and payload.startswith(b"%PDF-"):
        return "application/pdf"
    if suffix == ".png" and payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if suffix in {".jpg", ".jpeg"} and payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if suffix == ".txt" and _is_text(payload):
        return TXT_MIME
    raise ValueError("RIGHTS_EVIDENCE_TYPE_MISMATCH")


def _is_text(payload: bytes) -> bool:
    try:
        if payload.startswith(b"\xff\xfe") or payload.startswith(b"\xfe\xff"):
            payload.decode("utf-16")
        else:
            payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return True


def _as_utc_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("RIGHTS_DATE_TIMEZONE_REQUIRED")
        return value.astimezone(UTC)
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _evidence_view(evidence: RightsEvidence, artifact_id: str) -> RightsEvidenceView:
    return RightsEvidenceView(
        id=evidence.id,
        project_id=evidence.project_id,
        artifact_id=artifact_id,
        evidence_kind=EvidenceKind(evidence.evidence_kind),
        display_name=evidence.display_name,
        relative_path=evidence.relative_path,
        sha256=evidence.sha256,
        issuer=evidence.issuer,
        issued_at=evidence.issued_at,
        expires_at=evidence.expires_at,
    )


def _grant_view(grant: RightsGrant) -> RightsGrantView:
    return RightsGrantView(
        id=grant.id,
        project_id=grant.project_id,
        scope=RightsScope(grant.scope),
        territory=grant.territory,
        allows_ai_processing=grant.allows_ai_processing,
        allows_third_party_cloud=grant.allows_third_party_cloud,
        valid_from=grant.valid_from,
        expires_at=grant.expires_at,
        evidence_id=grant.evidence_id,
    )


def _artifact_id_for_evidence(session: Session, evidence: RightsEvidence) -> str:
    artifact_id = session.scalar(
        select(Artifact.id).where(
            Artifact.kind == ArtifactKind.RIGHTS_EVIDENCE.name,
            Artifact.relative_path == evidence.relative_path,
            Artifact.sha256 == evidence.sha256,
        )
    )
    if artifact_id is None:
        raise ValueError("RIGHTS_EVIDENCE_ARTIFACT_MISSING")
    return artifact_id
