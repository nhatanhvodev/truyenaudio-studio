from __future__ import annotations

import hashlib
import json
from pathlib import Path, PureWindowsPath
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import ArtifactKind, ArtifactStatus, new_id
from app.db.models import Artifact, AuditEvent


CHUNK_SIZE = 1024 * 1024


class ArtifactCache:
    def __init__(self, session: Session, artifact_root: Path | str) -> None:
        self.session = session
        self.artifact_root = Path(artifact_root).resolve()

    def lookup(
        self,
        kind: ArtifactKind | str,
        input_hash: str,
        settings_hash: str,
    ) -> Artifact | None:
        artifact = self.session.scalar(
            select(Artifact)
            .where(
                Artifact.kind == _enum_value(kind),
                Artifact.input_hash == input_hash,
                Artifact.settings_hash == settings_hash,
                Artifact.status == ArtifactStatus.READY.value,
            )
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        )
        if artifact is None:
            return None

        path = self._artifact_path(artifact.relative_path)
        reason: str | None = None
        actual_sha256: str | None = None
        actual_size: int | None = None
        if not path.is_file():
            reason = "missing"
        else:
            actual_size = path.stat().st_size
            if actual_size != artifact.byte_size:
                reason = "byte_size_mismatch"
            else:
                actual_sha256 = _sha256_file(path)
                if actual_sha256 != artifact.sha256:
                    reason = "sha256_mismatch"

        if reason is None:
            return artifact

        artifact.status = ArtifactStatus.CORRUPT.value
        self.session.add(
            AuditEvent(
                id=new_id(),
                actor="LOCAL_OWNER",
                action="ARTIFACT_CACHE_CORRUPT",
                entity_type="Artifact",
                entity_id=artifact.id,
                before_hash=artifact.sha256,
                after_hash=actual_sha256,
                redacted_details=canonical_json(
                    {
                        "reason": reason,
                        "relative_path": artifact.relative_path,
                        "expected_size": artifact.byte_size,
                        "actual_size": actual_size,
                    }
                ),
            )
        )
        self.session.flush()
        return None

    def _artifact_path(self, relative_path: str) -> Path:
        _validate_relative_path(relative_path)
        candidate = (self.artifact_root / relative_path).resolve()
        if not candidate.is_relative_to(self.artifact_root):
            raise ValueError("UNSAFE_ARTIFACT_PATH")
        return candidate


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_sha256(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_SIZE):
            sha256.update(chunk)
    return sha256.hexdigest()


def _enum_value(value: StrEnum | str) -> str:
    if isinstance(value, StrEnum):
        return value.value
    return str(value)


def _validate_relative_path(relative_path: str) -> None:
    if not relative_path.strip() or relative_path == ".":
        raise ValueError("UNSAFE_ARTIFACT_PATH")
    windows_path = PureWindowsPath(relative_path)
    local_path = Path(relative_path)
    if windows_path.is_absolute() or windows_path.drive or local_path.is_absolute():
        raise ValueError("UNSAFE_ARTIFACT_PATH")
    if ".." in windows_path.parts or ".." in local_path.parts:
        raise ValueError("UNSAFE_ARTIFACT_PATH")
