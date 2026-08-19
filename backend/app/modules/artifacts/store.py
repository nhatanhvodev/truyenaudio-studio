from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PureWindowsPath
import re
from types import TracebackType
from typing import BinaryIO
from uuid import uuid4

from app.contracts import ArtifactKind


CHUNK_SIZE = 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ArtifactStoreError(Exception):
    """Base exception for artifact store failures."""


class UnsafeArtifactPath(ArtifactStoreError):
    """Raised when an artifact path can escape the store root."""


class InvalidArtifactDigest(ArtifactStoreError):
    """Raised when a SHA-256 digest is not lowercase hex."""


class ArtifactAlreadyExists(ArtifactStoreError):
    """Raised when committing would overwrite an immutable artifact."""


class ArtifactWriterClosed(ArtifactStoreError):
    """Raised when committing an uncommitted writer after it was closed."""


@dataclass(frozen=True)
class ArtifactWrite:
    kind: ArtifactKind
    relative_path: str
    input_hash: str
    settings_hash: str
    mime_type: str

    def __post_init__(self) -> None:
        _validate_digest(self.input_hash, "input_hash")
        _validate_digest(self.settings_hash, "settings_hash")
        if not self.mime_type.strip():
            raise ValueError("mime_type must be non-empty")


@dataclass(frozen=True)
class StoredArtifact:
    relative_path: str
    sha256: str
    byte_size: int


class ArtifactStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def begin(self, write: ArtifactWrite) -> ArtifactWriter:
        final_path = self.resolve(write.relative_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path, file = self._open_unique_partial(final_path)
        return ArtifactWriter(
            relative_path=write.relative_path,
            final_path=final_path,
            partial_path=partial_path,
            file=file,
            store=self,
        )

    def resolve(self, relative_path: str | None = None) -> Path:
        if relative_path is None:
            return self.root

        _validate_relative_path(relative_path)
        candidate = (self.root / relative_path).resolve()
        if not candidate.is_relative_to(self.root):
            raise UnsafeArtifactPath(relative_path)
        return candidate

    def verify(self, relative_path: str, sha256: str) -> bool:
        _validate_digest(sha256, "sha256")
        artifact_path = self.resolve(relative_path)
        if not artifact_path.is_file():
            return False

        actual_sha256, _ = _sha256_file(artifact_path)
        return actual_sha256 == sha256

    def is_dir(self) -> bool:
        return self.root.is_dir()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Path):
            return self.root == other
        return super().__eq__(other)

    def _open_unique_partial(self, final_path: Path) -> tuple[Path, BinaryIO]:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY

        for _ in range(100):
            partial_path = final_path.with_name(f".{final_path.name}.{uuid4().hex}.partial")
            try:
                fd = os.open(partial_path, flags, 0o600)
            except FileExistsError:
                continue
            return partial_path, os.fdopen(fd, "wb")

        raise FileExistsError(f"could not create unique partial for {final_path}")


@dataclass
class ArtifactWriter:
    relative_path: str
    final_path: Path
    partial_path: Path
    file: BinaryIO
    store: ArtifactStore
    _committed_artifact: StoredArtifact | None = None

    def __enter__(self) -> ArtifactWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def commit(self) -> StoredArtifact:
        if self._committed_artifact is not None:
            return self._committed_artifact
        if self.file.closed:
            raise ArtifactWriterClosed(self.relative_path)

        self.file.flush()
        os.fsync(self.file.fileno())
        self.file.close()

        sha256, byte_size = _sha256_file(self.partial_path)
        self.store.resolve(self.relative_path)
        stored = StoredArtifact(
            relative_path=self.relative_path,
            sha256=sha256,
            byte_size=byte_size,
        )

        try:
            os.link(self.partial_path, self.final_path)
        except FileExistsError as exc:
            self._cleanup_partial()
            raise ArtifactAlreadyExists(self.relative_path) from exc
        except OSError:
            self._cleanup_partial()
            raise

        self._cleanup_partial()
        self._committed_artifact = stored
        return stored

    def close(self) -> None:
        if not self.file.closed:
            self.file.close()
        if self._committed_artifact is None:
            self._cleanup_partial()

    def _cleanup_partial(self) -> None:
        try:
            self.partial_path.unlink(missing_ok=True)
        except OSError:
            pass


def _validate_relative_path(relative_path: str) -> None:
    if not relative_path.strip() or relative_path == ".":
        raise UnsafeArtifactPath(relative_path)

    windows_path = PureWindowsPath(relative_path)
    local_path = Path(relative_path)
    if windows_path.is_absolute() or windows_path.drive or local_path.is_absolute():
        raise UnsafeArtifactPath(relative_path)

    if ".." in windows_path.parts or ".." in local_path.parts:
        raise UnsafeArtifactPath(relative_path)


def _validate_digest(digest: str, field_name: str) -> None:
    if not SHA256_PATTERN.fullmatch(digest):
        raise InvalidArtifactDigest(f"{field_name} must be lowercase SHA-256 hex")


def _sha256_file(path: Path) -> tuple[str, int]:
    sha256 = hashlib.sha256()
    byte_size = 0
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_SIZE):
            byte_size += len(chunk)
            sha256.update(chunk)
    return sha256.hexdigest(), byte_size
