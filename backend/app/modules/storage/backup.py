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
import threading
from uuid import uuid4

from app.settings.startup_lock import StartupLock


CHUNK_SIZE = 1024 * 1024
BACKUP_ID_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_.-]*$")
#: An artifact snapshot reports progress every N artifacts...
PROGRESS_ARTIFACT_INTERVAL = 10
#: ...or as soon as this many artifact bytes were processed, whichever comes first.
PROGRESS_BYTE_INTERVAL = 8 * 1024 * 1024
#: Ordered create() phases: a caller may assert that reported phases never go backwards.
BACKUP_PROGRESS_PHASES: tuple[str, ...] = (
    "starting",
    "database",
    "artifacts",
    "manifest",
    "verifying",
    "completed",
)


class BackupError(RuntimeError):
    pass


class BackupVerificationError(BackupError):
    pass


class RestoreLockRequired(BackupError):
    pass


class RestoreConfirmationRequired(BackupError):
    """A destructive restore/copy target must be confirmed verbatim (U10)."""


class RetentionCountInvalid(BackupError):
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


@dataclass(frozen=True)
class BackupSummary:
    id: str
    sha256: str
    byte_size: int
    verified: bool
    verification_error: str | None


@dataclass(frozen=True)
class RetentionPlan:
    """Preview of a retention change: **nothing is deleted** by this plan (U10)."""

    requested_count: int
    current_count: int
    kept: tuple[str, ...]
    deletable: tuple[str, ...]
    applied: bool
    requires_confirmation: bool
    prune_on_create: bool


@dataclass(frozen=True)
class BackupProgress:
    """One observable milestone of an in-flight :meth:`BackupService.create`.

    ``copied_artifacts``/``copied_bytes`` count the artifacts already materialised
    into the snapshot (hardlinked *or* copied), so a UI can render ``copied/total``
    directly against the matching ``total_*`` values. ``message`` carries detail that
    has no counter of its own (for example the database byte size after the online
    backup step).
    """

    phase: str
    copied_artifacts: int
    total_artifacts: int
    copied_bytes: int
    total_bytes: int
    message: str | None = None


class BackupProgressTracker:
    """In-process, thread-safe view of the ``create()`` running right now.

    The instance *is* the progress callback: pass it to
    ``BackupService.create(progress=tracker)`` and read :meth:`snapshot` from another
    thread (the storage API does) to render a progress bar. ``snapshot()`` returns
    ``None`` while no create is in flight, which the route reports as ``204``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._progress: BackupProgress | None = None

    def begin(self) -> None:
        self.update(BackupProgress("starting", 0, 0, 0, 0, "backup starting"))

    def end(self) -> None:
        with self._lock:
            self._progress = None

    def update(self, progress: BackupProgress) -> None:
        with self._lock:
            self._progress = progress

    def __call__(self, progress: BackupProgress) -> None:
        self.update(progress)

    def snapshot(self) -> BackupProgress | None:
        with self._lock:
            return self._progress


@dataclass(frozen=True)
class _ArtifactEntry:
    """One artifact of the database snapshot that must exist in the backup snapshot."""

    relative_path: str
    expected_sha256: str
    source_path: Path
    snapshot_relative_path: str
    byte_size: int


@dataclass(frozen=True)
class _LinkBase:
    """Newest previous backup whose snapshot may donate unchanged artifacts."""

    backup_id: str
    snapshot_root: Path
    artifact_index: dict[str, tuple[str, str]]


@dataclass(frozen=True)
class _SnapshotStats:
    artifact_count: int
    linked_artifact_count: int
    copied_artifact_count: int
    total_bytes: int
    base_backup_id: str | None
    storage_by_path: dict[str, str]


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
        progress_callback: Callable[[BackupProgress], None] | None = None,
        incremental: bool = True,
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
        self.progress_callback = progress_callback
        self.incremental = incremental

    def create(self, *, progress: Callable[[BackupProgress], None] | None = None) -> BackupView:
        """Write one verified, self-contained backup and report its real milestones.

        ``progress`` (falling back to the constructor's ``progress_callback``) is called
        at every milestone that actually happens: ``starting``, ``database`` (carrying
        the database byte size), ``artifacts`` (per batch, with copied/total counts and
        bytes), ``manifest``, ``verifying`` and ``completed``.

        A callback that raises aborts the backup: the exception propagates and the
        partial files are removed like for any other failure. Swallowing it would let a
        broken progress hook silently produce a backup nobody can observe, so the
        service fails fast on purpose.

        Artifacts that the newest previous snapshot already holds with the same checksum
        are hardlinked instead of copied (``incremental``). That stays self-contained:
        the new snapshot owns its own directory entry, so pruning the older backup keeps
        the data alive for this one (see :meth:`_prune_backups`).
        """
        report = progress or self.progress_callback or _ignore_progress
        self.backup_root.mkdir(parents=True, exist_ok=True)
        backup_id = f"{self.clock()}-{uuid4().hex[:8]}"
        temp_path = self.backup_root / f".{backup_id}.sqlite3.partial"
        final_path = self.backup_root / f"{backup_id}.sqlite3"
        temp_manifest = self.backup_root / f".{backup_id}.manifest.json.partial"
        final_manifest = self.backup_root / f"{backup_id}.manifest.json"
        temp_artifact_root = self.backup_root / f".{backup_id}.artifacts.partial"
        final_artifact_root = self._artifact_snapshot_path(backup_id)

        report(BackupProgress("starting", 0, 0, 0, 0, f"backup {backup_id} starting"))
        try:
            with closing(sqlite3.connect(self.db_path)) as source, closing(sqlite3.connect(temp_path)) as target:
                source.backup(target)
            integrity_check = _integrity_check(temp_path)
            if integrity_check != "ok":
                raise BackupVerificationError(f"backup integrity failed: {integrity_check}")
            entries = self._artifact_entries(temp_path, snapshot_root=temp_artifact_root)
            total_artifacts = len(entries)
            total_bytes = sum(entry.byte_size for entry in entries)
            report(
                BackupProgress(
                    "database",
                    0,
                    total_artifacts,
                    0,
                    total_bytes,
                    f"database backed up: {temp_path.stat().st_size} bytes",
                )
            )
            base = self._link_base()
            stats = self._materialize_artifacts(
                entries,
                snapshot_root=temp_artifact_root,
                base=base,
                report=report,
            )
            try:
                artifact_count = self._verify_artifact_pointers(temp_path, artifact_root=temp_artifact_root)
            except BackupVerificationError:
                if stats.linked_artifact_count == 0:
                    raise
                # A hardlinked inode is owned by another backup directory; re-copy only the
                # linked files that no longer match instead of publishing inherited
                # corruption (and instead of throwing the good links away).
                stats = self._repair_linked_artifacts(entries, snapshot_root=temp_artifact_root, stats=stats)
                artifact_count = self._verify_artifact_pointers(temp_path, artifact_root=temp_artifact_root)
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
                    # Additive (v1 readers ignore them): what this snapshot reused from
                    # which previous backup, and where every artifact actually lives.
                    "linked_artifact_count": stats.linked_artifact_count,
                    "copied_artifact_count": stats.copied_artifact_count,
                    "base_backup_id": stats.base_backup_id,
                    "artifact_index": _artifact_index_payload(entries, stats.storage_by_path),
                },
            )
            os.replace(temp_path, final_path)
            os.replace(temp_artifact_root, final_artifact_root)
            os.replace(temp_manifest, final_manifest)
        finally:
            temp_path.unlink(missing_ok=True)
            temp_manifest.unlink(missing_ok=True)
            shutil.rmtree(temp_artifact_root, ignore_errors=True)

        report(
            BackupProgress(
                "manifest",
                total_artifacts,
                total_artifacts,
                total_bytes,
                total_bytes,
                f"manifest written: {artifact_count} artifacts, "
                f"{stats.linked_artifact_count} linked / {stats.copied_artifact_count} copied",
            )
        )
        view = BackupView(backup_id, final_path, final_manifest, sha256, byte_size, integrity_check)
        report(
            BackupProgress(
                "verifying",
                total_artifacts,
                total_artifacts,
                total_bytes,
                total_bytes,
                f"verifying {backup_id}",
            )
        )
        self.verify(backup_id)
        self._prune_backups()
        report(
            BackupProgress(
                "completed",
                total_artifacts,
                total_artifacts,
                total_bytes,
                total_bytes,
                f"backup {backup_id} complete",
            )
        )
        return view

    def verify(self, backup_id: str) -> BackupVerification:
        manifest = self._read_manifest(backup_id)
        backup_path = self._backup_path(backup_id)
        if not backup_path.is_file():
            raise BackupVerificationError(f"backup file is missing: {backup_id}")
        sha256, byte_size = _sha256_file(backup_path)
        try:
            integrity_check = _integrity_check(backup_path)
        except sqlite3.DatabaseError as exc:
            # A truncated/replaced backup file is a verification failure, never a crash.
            raise BackupVerificationError(f"backup file is not a readable database: {backup_id}") from exc
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

    def list_backups(self) -> tuple[BackupSummary, ...]:
        """Newest-first backups with their verification result (checksum + integrity)."""
        summaries: list[BackupSummary] = []
        for manifest_path in self._manifest_paths():
            backup_id = manifest_path.name.removesuffix(".manifest.json")
            try:
                verification = self.verify(backup_id)
            except BackupVerificationError as exc:
                summaries.append(BackupSummary(backup_id, "", 0, False, str(exc)))
                continue
            summaries.append(
                BackupSummary(backup_id, verification.sha256, verification.byte_size, True, None)
            )
        return tuple(summaries)

    def retention_plan(self, requested_count: int) -> RetentionPlan:
        """Preview what a retention setting would keep/delete — without deleting anything.

        ``create()`` still prunes on every backup (bounded backup directory), which is
        reported as ``prune_on_create`` so the UI can explain the difference instead of
        silently shrinking the window when the number changes.
        """
        if isinstance(requested_count, bool) or not isinstance(requested_count, int) or requested_count < 1:
            raise RetentionCountInvalid("RETENTION_COUNT_INVALID")
        ids = [path.name.removesuffix(".manifest.json") for path in self._manifest_paths()]
        kept = tuple(ids[:requested_count])
        deletable = tuple(ids[requested_count:])
        return RetentionPlan(
            requested_count=requested_count,
            current_count=len(ids),
            kept=kept,
            deletable=deletable,
            applied=False,
            requires_confirmation=bool(deletable),
            prune_on_create=True,
        )

    def restore_copy(
        self,
        backup_id: str,
        target_data_root: Path | str,
        *,
        confirm_target: str | None,
        lock_token: RestoreLockToken | None = None,
    ) -> RestoreView:
        """Restore a verified backup into a **new isolated copy** (U10).

        The caller must repeat the exact target path in ``confirm_target``: a copy
        restore creates a whole data root, so the destination is never guessed.
        """
        target = Path(target_data_root)
        if confirm_target is None or confirm_target.strip() != str(target):
            raise RestoreConfirmationRequired("RESTORE_TARGET_CONFIRMATION_REQUIRED")
        return self.restore_to_data_root(backup_id, target, lock_token=lock_token)

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
        if _paths_overlap(source_root, target_root):
            raise BackupError("isolated restore target must not overlap the source data root")
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

    def _artifact_entries(self, db_path: Path, *, snapshot_root: Path) -> list[_ArtifactEntry]:
        """Resolve every artifact pointer of the database snapshot to a real live file."""
        root = snapshot_root.resolve()
        entries: list[_ArtifactEntry] = []
        for relative_path, expected_sha256 in self._artifact_rows(db_path):
            source_path = self._resolve_artifact_pointer(
                relative_path, expected_sha256, artifact_root=self.artifact_root
            )
            if source_path is None:
                raise BackupVerificationError(f"artifact pointer missing: {relative_path}")
            snapshot_relative_path = source_path.relative_to(self.artifact_root).as_posix()
            snapshot_path = (root / snapshot_relative_path).resolve()
            if not snapshot_path.is_relative_to(root):
                raise BackupVerificationError(f"artifact snapshot escapes root: {relative_path}")
            entries.append(
                _ArtifactEntry(
                    relative_path=relative_path,
                    expected_sha256=expected_sha256,
                    source_path=source_path,
                    snapshot_relative_path=snapshot_relative_path,
                    byte_size=source_path.stat().st_size,
                )
            )
        return entries

    def _materialize_artifacts(
        self,
        entries: list[_ArtifactEntry],
        *,
        snapshot_root: Path,
        base: _LinkBase | None,
        report: Callable[[BackupProgress], None],
    ) -> _SnapshotStats:
        """Build the artifact snapshot, hardlinking unchanged files from ``base``."""
        snapshot_root.mkdir(parents=True, exist_ok=True)
        root = snapshot_root.resolve()
        total_bytes = sum(entry.byte_size for entry in entries)
        linked = 0
        copied = 0
        processed_bytes = 0
        reported_bytes = 0
        storage_by_path: dict[str, str] = {}
        for index, entry in enumerate(entries, start=1):
            snapshot_path = (root / entry.snapshot_relative_path).resolve()
            if not snapshot_path.is_relative_to(root):
                raise BackupVerificationError(f"artifact snapshot escapes root: {entry.relative_path}")
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            if base is not None and self._link_artifact(entry, base=base, snapshot_path=snapshot_path):
                linked += 1
                storage_by_path[entry.relative_path] = "linked"
            else:
                shutil.copyfile(entry.source_path, snapshot_path)
                copied += 1
                storage_by_path[entry.relative_path] = "copied"
            processed_bytes += entry.byte_size
            if (
                index % PROGRESS_ARTIFACT_INTERVAL == 0
                or index == len(entries)
                or processed_bytes - reported_bytes >= PROGRESS_BYTE_INTERVAL
            ):
                reported_bytes = processed_bytes
                report(
                    BackupProgress(
                        "artifacts",
                        index,
                        len(entries),
                        processed_bytes,
                        total_bytes,
                        f"{linked} linked / {copied} copied of {len(entries)} artifacts",
                    )
                )
        if not entries:
            report(BackupProgress("artifacts", 0, 0, 0, 0, "no artifacts to snapshot"))
        return _SnapshotStats(
            artifact_count=len(entries),
            linked_artifact_count=linked,
            copied_artifact_count=copied,
            total_bytes=total_bytes,
            base_backup_id=base.backup_id if base is not None else None,
            storage_by_path=storage_by_path,
        )

    def _repair_linked_artifacts(
        self,
        entries: list[_ArtifactEntry],
        *,
        snapshot_root: Path,
        stats: _SnapshotStats,
    ) -> _SnapshotStats:
        """Re-copy the linked files that no longer match their checksum, keeping the rest.

        Only called after the snapshot verification rejected at least one linked file, so
        the surviving links stay hardlinks and the damaged ones are rebuilt from the live
        artifact. The reported counts follow what is actually on disk afterwards.
        """
        root = snapshot_root.resolve()
        storage_by_path = dict(stats.storage_by_path)
        linked = stats.linked_artifact_count
        copied = stats.copied_artifact_count
        for entry in entries:
            if storage_by_path.get(entry.relative_path) != "linked":
                continue
            snapshot_path = (root / entry.snapshot_relative_path).resolve()
            if snapshot_path.is_file() and _sha256_file(snapshot_path)[0] == entry.expected_sha256:
                continue
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.unlink(missing_ok=True)
            shutil.copyfile(entry.source_path, snapshot_path)
            storage_by_path[entry.relative_path] = "copied"
            linked -= 1
            copied += 1
        return _SnapshotStats(
            artifact_count=stats.artifact_count,
            linked_artifact_count=linked,
            copied_artifact_count=copied,
            total_bytes=stats.total_bytes,
            base_backup_id=stats.base_backup_id,
            storage_by_path=storage_by_path,
        )

    def _link_artifact(self, entry: _ArtifactEntry, *, base: _LinkBase, snapshot_path: Path) -> bool:
        """Link ``entry`` from the base snapshot when it provably holds the same content.

        The decision trusts the base manifest's recorded checksum for this path; a base
        file that no longer holds that content is caught by the pointer verification that
        runs right after materialisation, which then re-copies those files from the live
        artifacts. So the base manifest can only cost work, never integrity.
        """
        base_entry = base.artifact_index.get(entry.relative_path)
        if base_entry is None:
            return False
        base_relative_path, base_sha256 = base_entry
        if base_sha256 != entry.expected_sha256:
            return False
        base_root = base.snapshot_root.resolve()
        base_path = (base_root / base_relative_path).resolve()
        if not base_path.is_relative_to(base_root) or not base_path.is_file():
            return False
        try:
            os.link(base_path, snapshot_path)
        except OSError:
            # Cross-volume or a filesystem without hardlinks: a plain copy is always right.
            return False
        return True

    def _link_base(self) -> _LinkBase | None:
        """Newest previous backup usable as a hardlink donor, or None to copy everything."""
        if not self.incremental:
            return None
        for manifest_path in self._manifest_paths():
            backup_id = manifest_path.name.removesuffix(".manifest.json")
            try:
                manifest = self._read_manifest(backup_id)
            except (BackupError, OSError, ValueError):
                continue
            artifact_index = _manifest_artifact_index(manifest)
            if not artifact_index:
                continue
            snapshot_root = self._artifact_snapshot_path(backup_id)
            if not snapshot_root.is_dir():
                continue
            return _LinkBase(backup_id, snapshot_root, artifact_index)
        return None

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

    def _manifest_paths(self) -> list[Path]:
        return sorted(
            self.backup_root.glob("*.manifest.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def _prune_backups(self) -> None:
        """Delete backups beyond the retention count.

        Safe for hardlinked snapshots: removing a donor directory only drops its own
        directory entries, so a newer snapshot that linked those inodes keeps its data and
        its verification stays valid.
        """
        manifests = self._manifest_paths()
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


def _ignore_progress(_progress: BackupProgress) -> None:
    """Default progress hook: a caller with no reporter is a no-op, not an error."""


def _artifact_index_payload(
    entries: list[_ArtifactEntry],
    storage_by_path: dict[str, str],
) -> dict[str, object]:
    """Per-artifact record so a backup states what it linked and what it copied."""
    return {
        entry.relative_path: {
            "sha256": entry.expected_sha256,
            "snapshot_path": entry.snapshot_relative_path,
            "byte_size": entry.byte_size,
            "storage": storage_by_path.get(entry.relative_path, "copied"),
        }
        for entry in entries
    }


def _manifest_artifact_index(manifest: dict[str, object]) -> dict[str, tuple[str, str]]:
    """Read a manifest's additive artifact index; older manifests simply have none."""
    raw = manifest.get("artifact_index")
    if not isinstance(raw, dict):
        return {}
    index: dict[str, tuple[str, str]] = {}
    for relative_path, value in raw.items():
        if not isinstance(value, dict):
            continue
        sha256 = value.get("sha256")
        snapshot_path = value.get("snapshot_path")
        if isinstance(sha256, str) and isinstance(snapshot_path, str):
            index[str(relative_path)] = (snapshot_path, sha256)
    return index


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _paths_overlap(first: Path, second: Path) -> bool:
    return first.is_relative_to(second) or second.is_relative_to(first)


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
