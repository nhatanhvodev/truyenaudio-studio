from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.settings.startup_lock import StartupLock


CHUNK_SIZE = 1024 * 1024


class BackupError(RuntimeError):
    pass


class BackupVerificationError(BackupError):
    pass


class RestoreLockRequired(BackupError):
    pass


@dataclass(frozen=True)
class BackupView:
    id: str
    path: Path
    manifest_path: Path
    sha256: str
    byte_size: int
    integrity_check: str


@dataclass(frozen=True)
class BackupVerification:
    id: str
    ok: bool
    integrity_check: str
    sha256: str
    byte_size: int


@dataclass(frozen=True)
class RestoreView:
    backup_id: str
    target_path: Path
    pre_restore_backup_path: Path | None
    integrity_check: str
    artifact_pointer_count: int


@dataclass
class RestoreLockToken:
    api_lock: StartupLock
    worker_lock: StartupLock
    active: bool = False

    def __enter__(self) -> RestoreLockToken:
        self.api_lock.acquire()
        try:
            self.worker_lock.acquire()
        except Exception:
            self.api_lock.release()
            raise
        self.active = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.active = False
        self.worker_lock.release()
        self.api_lock.release()


class BackupService:
    def __init__(
        self,
        *,
        db_path: Path | str,
        backup_root: Path | str,
        artifact_root: Path | str,
        retention_count: int = 7,
        api_lock_path: Path | str | None = None,
        worker_lock_path: Path | str | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        if retention_count < 1:
            raise ValueError("retention_count must be positive")
        self.db_path = Path(db_path)
        self.backup_root = Path(backup_root)
        self.artifact_root = Path(artifact_root).resolve()
        self.retention_count = retention_count
        self.api_lock_path = Path(api_lock_path) if api_lock_path is not None else self.db_path.parent / "studio-api.lock"
        self.worker_lock_path = (
            Path(worker_lock_path) if worker_lock_path is not None else self.db_path.parent / "studio-worker.lock"
        )
        self.clock = clock or _utc_stamp

    def create(self) -> BackupView:
        self.backup_root.mkdir(parents=True, exist_ok=True)
        backup_id = f"{self.clock()}-{uuid4().hex[:8]}"
        temp_path = self.backup_root / f".{backup_id}.sqlite3.partial"
        final_path = self.backup_root / f"{backup_id}.sqlite3"
        temp_manifest = self.backup_root / f".{backup_id}.manifest.json.partial"
        final_manifest = self.backup_root / f"{backup_id}.manifest.json"

        try:
            with closing(sqlite3.connect(self.db_path)) as source, closing(sqlite3.connect(temp_path)) as target:
                source.backup(target)
            integrity_check = _integrity_check(temp_path)
            if integrity_check != "ok":
                raise BackupVerificationError(f"backup integrity failed: {integrity_check}")
            sha256, byte_size = _sha256_file(temp_path)
            _write_json(
                temp_manifest,
                {
                    "id": backup_id,
                    "schema_version": "truyenaudio-studio.backup.v1",
                    "database": final_path.name,
                    "sha256": sha256,
                    "byte_size": byte_size,
                    "integrity_check": integrity_check,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            os.replace(temp_path, final_path)
            os.replace(temp_manifest, final_manifest)
        finally:
            temp_path.unlink(missing_ok=True)
            temp_manifest.unlink(missing_ok=True)

        view = BackupView(backup_id, final_path, final_manifest, sha256, byte_size, integrity_check)
        self.verify(backup_id)
        self._prune_backups()
        return view

    def verify(self, backup_id: str) -> BackupVerification:
        manifest = self._read_manifest(backup_id)
        backup_path = self._backup_path(backup_id)
        if not backup_path.is_file():
            raise BackupVerificationError(f"backup file is missing: {backup_id}")
        sha256, byte_size = _sha256_file(backup_path)
        integrity_check = _integrity_check(backup_path)
        ok = (
            sha256 == manifest["sha256"]
            and byte_size == manifest["byte_size"]
            and integrity_check == manifest["integrity_check"] == "ok"
        )
        if not ok:
            raise BackupVerificationError(f"backup verification failed: {backup_id}")
        return BackupVerification(backup_id, True, integrity_check, sha256, byte_size)

    def acquire_restore_locks(self) -> RestoreLockToken:
        return RestoreLockToken(StartupLock(self.api_lock_path), StartupLock(self.worker_lock_path))

    def restore_to(
        self,
        backup_id: str,
        target_path: Path | str | None = None,
        *,
        lock_token: RestoreLockToken | None = None,
    ) -> RestoreView:
        if lock_token is None or not lock_token.active:
            raise RestoreLockRequired("restore requires held API and worker startup locks")

        self.verify(backup_id)
        backup_path = self._backup_path(backup_id)
        target = Path(target_path) if target_path is not None else self.db_path
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_restore = target.with_name(f".{target.name}.{uuid4().hex}.restore")
        pre_restore_backup = target.with_name(f"{target.name}.pre-restore-{_utc_stamp()}")

        try:
            with closing(sqlite3.connect(backup_path)) as source, closing(sqlite3.connect(temp_restore)) as restored:
                source.backup(restored)
            integrity_check = _integrity_check(temp_restore)
            if integrity_check != "ok":
                raise BackupVerificationError(f"restore integrity failed: {integrity_check}")
            pointer_count = self._verify_artifact_pointers(temp_restore)
            if target.exists():
                with closing(sqlite3.connect(target)) as source, closing(sqlite3.connect(pre_restore_backup)) as preserved:
                    source.backup(preserved)
                if _integrity_check(pre_restore_backup) != "ok":
                    raise BackupVerificationError("pre-restore backup integrity failed")
            else:
                pre_restore_backup = None
            os.replace(temp_restore, target)
        finally:
            temp_restore.unlink(missing_ok=True)

        return RestoreView(backup_id, target, pre_restore_backup, integrity_check, pointer_count)

    def _verify_artifact_pointers(self, db_path: Path) -> int:
        with closing(sqlite3.connect(db_path)) as connection:
            rows = connection.execute(
                "SELECT relative_path, sha256 FROM artifacts WHERE status != 'DELETED'"
            ).fetchall()

        for relative_path, expected_sha256 in rows:
            path = (self.artifact_root / relative_path).resolve()
            if not path.is_relative_to(self.artifact_root):
                raise BackupVerificationError(f"artifact pointer escapes root: {relative_path}")
            if not path.is_file():
                raise BackupVerificationError(f"artifact pointer missing: {relative_path}")
            actual_sha256, _ = _sha256_file(path)
            if actual_sha256 != expected_sha256:
                raise BackupVerificationError(f"artifact pointer checksum mismatch: {relative_path}")
        return len(rows)

    def _prune_backups(self) -> None:
        manifests = sorted(self.backup_root.glob("*.manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for manifest_path in manifests[self.retention_count :]:
            backup_id = manifest_path.name.removesuffix(".manifest.json")
            self._backup_path(backup_id).unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)

    def _read_manifest(self, backup_id: str) -> dict[str, object]:
        path = self._manifest_path(backup_id)
        if not path.is_file():
            raise BackupVerificationError(f"backup manifest is missing: {backup_id}")
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _backup_path(self, backup_id: str) -> Path:
        return self.backup_root / f"{backup_id}.sqlite3"

    def _manifest_path(self, backup_id: str) -> Path:
        return self.backup_root / f"{backup_id}.manifest.json"


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _integrity_check(path: Path) -> str:
    with closing(sqlite3.connect(path)) as connection:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def _sha256_file(path: Path) -> tuple[str, int]:
    sha256 = hashlib.sha256()
    byte_size = 0
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_SIZE):
            byte_size += len(chunk)
            sha256.update(chunk)
    return sha256.hexdigest(), byte_size


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with path.open("xb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
