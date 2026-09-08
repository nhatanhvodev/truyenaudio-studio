from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from uuid import uuid4

from app.settings.startup_lock import StartupLock


CHUNK_SIZE = 1024 * 1024
BACKUP_ID_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_.-]*$")


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
        self.backup_root = Path(backup_root).resolve()
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
        temp_artifact_root = self.backup_root / f".{backup_id}.artifacts.partial"
        final_artifact_root = self._artifact_snapshot_path(backup_id)

        try:
            with closing(sqlite3.connect(self.db_path)) as source, closing(sqlite3.connect(temp_path)) as target:
                source.backup(target)
            integrity_check = _integrity_check(temp_path)
            if integrity_check != "ok":
                raise BackupVerificationError(f"backup integrity failed: {integrity_check}")
            artifact_count = self._copy_artifact_snapshot(temp_path, temp_artifact_root)
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
                    "artifact_snapshot": final_artifact_root.name,
                    "artifact_count": artifact_count,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            os.replace(temp_path, final_path)
            os.replace(temp_artifact_root, final_artifact_root)
            os.replace(temp_manifest, final_manifest)
        finally:
            temp_path.unlink(missing_ok=True)
            temp_manifest.unlink(missing_ok=True)
            shutil.rmtree(temp_artifact_root, ignore_errors=True)

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
        artifact_count = self._verify_artifact_pointers(backup_path, artifact_root=self._artifact_snapshot_path(backup_id))
        ok = (
            sha256 == manifest["sha256"]
            and byte_size == manifest["byte_size"]
            and integrity_check == manifest["integrity_check"] == "ok"
            and artifact_count == manifest.get("artifact_count")
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
            pointer_count = self._verify_artifact_pointers(temp_restore, artifact_root=self.artifact_root)
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

    def restore_to_data_root(
        self,
        backup_id: str,
        target_data_root: Path | str,
        *,
        lock_token: RestoreLockToken | None = None,
    ) -> RestoreView:
        if lock_token is None or not lock_token.active:
            raise RestoreLockRequired("restore requires held API and worker startup locks")

        target_root = Path(target_data_root).resolve()
        source_root = self.db_path.parent.resolve()
        if target_root == source_root:
            raise BackupError("isolated restore target must differ from the source data root")
        if target_root.exists():
            raise BackupError(f"isolated restore target already exists: {target_root}")

        self.verify(backup_id)
        backup_path = self._backup_path(backup_id)
        snapshot_root = self._artifact_snapshot_path(backup_id)
        staging_root = target_root.parent / f".{target_root.name}.{uuid4().hex}.restore"
        target_path = target_root / "studio.sqlite3"

        try:
            staging_root.mkdir(parents=True)
            staged_db = staging_root / target_path.name
            with closing(sqlite3.connect(backup_path)) as source, closing(sqlite3.connect(staged_db)) as restored:
                source.backup(restored)
            integrity_check = _integrity_check(staged_db)
            if integrity_check != "ok":
                raise BackupVerificationError(f"restore integrity failed: {integrity_check}")
            shutil.copytree(snapshot_root, staging_root, dirs_exist_ok=True)
            pointer_count = self._verify_artifact_pointers(staged_db, artifact_root=staging_root)
            os.replace(staging_root, target_root)
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

        return RestoreView(backup_id, target_path, None, integrity_check, pointer_count)

    def _copy_artifact_snapshot(self, db_path: Path, snapshot_root: Path) -> int:
        snapshot_root.mkdir(parents=True, exist_ok=True)
        for relative_path, expected_sha256 in self._artifact_rows(db_path):
            source_path = self._resolve_artifact_pointer(relative_path, expected_sha256, artifact_root=self.artifact_root)
            if source_path is None:
                raise BackupVerificationError(f"artifact pointer missing: {relative_path}")
            snapshot_path = (snapshot_root / source_path.relative_to(self.artifact_root)).resolve()
            if not snapshot_path.is_relative_to(snapshot_root.resolve()):
                raise BackupVerificationError(f"artifact snapshot escapes root: {relative_path}")
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, snapshot_path)
        return self._verify_artifact_pointers(db_path, artifact_root=snapshot_root)

    def _verify_artifact_pointers(self, db_path: Path, *, artifact_root: Path) -> int:
        if not artifact_root.is_dir():
            raise BackupVerificationError(f"artifact snapshot is missing: {artifact_root}")
        for relative_path, expected_sha256 in self._artifact_rows(db_path):
            path = self._resolve_artifact_pointer(relative_path, expected_sha256, artifact_root=artifact_root)
            if path is None:
                raise BackupVerificationError(f"artifact pointer missing: {relative_path}")
        return len(self._artifact_rows(db_path))

    @staticmethod
    def _artifact_rows(db_path: Path) -> list[tuple[str, str]]:
        with closing(sqlite3.connect(db_path)) as connection:
            rows = connection.execute(
                "SELECT relative_path, sha256 FROM artifacts WHERE status != 'DELETED'"
            ).fetchall()
        return [(str(relative_path), str(expected_sha256)) for relative_path, expected_sha256 in rows]

    def _resolve_artifact_pointer(self, relative_path: str, expected_sha256: str, *, artifact_root: Path) -> Path | None:
        candidates = self._artifact_pointer_candidates(relative_path, artifact_root=artifact_root)
        found_file = False
        for path in candidates:
            if not path.is_relative_to(artifact_root):
                raise BackupVerificationError(f"artifact pointer escapes root: {relative_path}")
            if not path.is_file():
                continue
            found_file = True
            actual_sha256, _ = _sha256_file(path)
            if actual_sha256 == expected_sha256:
                return path
        if found_file:
            raise BackupVerificationError(f"artifact pointer checksum mismatch: {relative_path}")
        return None

    @staticmethod
    def _artifact_pointer_candidates(relative_path: str, *, artifact_root: Path) -> tuple[Path, ...]:
        primary = (artifact_root / relative_path).resolve()
        if artifact_root.name == "artifacts":
            return (primary,)
        secondary = (artifact_root / "artifacts" / relative_path).resolve()
        if secondary == primary:
            return (primary,)
        return (primary, secondary)

    def _prune_backups(self) -> None:
        manifests = sorted(self.backup_root.glob("*.manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for manifest_path in manifests[self.retention_count :]:
            backup_id = manifest_path.name.removesuffix(".manifest.json")
            self._backup_path(backup_id).unlink(missing_ok=True)
            shutil.rmtree(self._artifact_snapshot_path(backup_id), ignore_errors=True)
            manifest_path.unlink(missing_ok=True)

    def _read_manifest(self, backup_id: str) -> dict[str, object]:
        path = self._manifest_path(backup_id)
        if not path.is_file():
            raise BackupVerificationError(f"backup manifest is missing: {backup_id}")
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _backup_path(self, backup_id: str) -> Path:
        return self._contained_backup_path(backup_id, ".sqlite3")

    def _manifest_path(self, backup_id: str) -> Path:
        return self._contained_backup_path(backup_id, ".manifest.json")

    def _artifact_snapshot_path(self, backup_id: str) -> Path:
        return self._contained_backup_path(backup_id, ".artifacts")

    def _contained_backup_path(self, backup_id: str, suffix: str) -> Path:
        if not BACKUP_ID_PATTERN.fullmatch(backup_id) or ".." in backup_id:
            raise BackupVerificationError(f"invalid backup id: {backup_id}")
        path = (self.backup_root / f"{backup_id}{suffix}").resolve()
        if not path.is_relative_to(self.backup_root):
            raise BackupVerificationError(f"backup path escapes root: {backup_id}")
        return path


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
