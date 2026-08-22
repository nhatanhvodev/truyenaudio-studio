from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import text

from app.modules.storage.backup import BackupService, BackupVerificationError, RestoreLockRequired


HASH_B = "b" * 64
HASH_C = "c" * 64


def test_online_backup_restores_pointers(migrated_engine, tmp_path: Path) -> None:
    db_path = Path(migrated_engine.url.database)
    artifact_root = tmp_path / "artifacts"
    artifact_path = artifact_root / "chapters" / "one" / "master.mp3"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"approved mp3")
    artifact_hash = _sha256_bytes(b"approved mp3")
    _seed_artifact_row(migrated_engine, artifact_path.relative_to(artifact_root).as_posix(), artifact_hash)

    service = BackupService(
        db_path=db_path,
        backup_root=tmp_path / "backups",
        artifact_root=artifact_root,
        api_lock_path=tmp_path / "studio-api.lock",
        worker_lock_path=tmp_path / "studio-worker.lock",
    )
    backup = service.create()
    restore_path = tmp_path / "restored.sqlite3"

    with service.acquire_restore_locks() as token:
        restored = service.restore_to(backup.id, restore_path, lock_token=token)

    assert restored.artifact_pointer_count == 1
    assert restored.integrity_check == "ok"
    with sqlite3.connect(restore_path) as connection:
        assert connection.execute("SELECT relative_path FROM artifacts").fetchone()[0] == "chapters/one/master.mp3"


def test_restore_verifies_rows_under_data_root_artifacts_when_paths_are_artifact_relative(
    migrated_engine,
    tmp_path: Path,
) -> None:
    db_path = Path(migrated_engine.url.database)
    data_root = tmp_path
    artifact_path = data_root / "artifacts" / "audio" / "chapter-1.mp3"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"speech artifact")
    _seed_artifact_row(
        migrated_engine,
        "audio/chapter-1.mp3",
        _sha256_bytes(b"speech artifact"),
    )

    service = BackupService(
        db_path=db_path,
        backup_root=data_root / "backups",
        artifact_root=data_root,
        api_lock_path=data_root / "studio-api.lock",
        worker_lock_path=data_root / "studio-worker.lock",
    )
    backup = service.create()

    with service.acquire_restore_locks() as token:
        restored = service.restore_to(backup.id, data_root / "restored.sqlite3", lock_token=token)

    assert restored.artifact_pointer_count == 1


def test_restore_requires_api_and_worker_startup_locks(migrated_engine, tmp_path: Path) -> None:
    db_path = Path(migrated_engine.url.database)
    service = BackupService(
        db_path=db_path,
        backup_root=tmp_path / "backups",
        artifact_root=tmp_path / "artifacts",
        api_lock_path=tmp_path / "studio-api.lock",
        worker_lock_path=tmp_path / "studio-worker.lock",
    )
    backup = service.create()

    with pytest.raises(RestoreLockRequired):
        service.restore_to(backup.id, tmp_path / "restored.sqlite3")


def test_backup_manifest_is_verified_and_retains_newest_seven(migrated_engine, tmp_path: Path) -> None:
    db_path = Path(migrated_engine.url.database)
    service = BackupService(
        db_path=db_path,
        backup_root=tmp_path / "backups",
        artifact_root=tmp_path / "artifacts",
        retention_count=7,
        clock=_IncrementingClock(),
    )

    backups = [service.create() for _ in range(8)]

    assert service.verify(backups[-1].id).ok
    assert not (tmp_path / "backups" / f"{backups[0].id}.sqlite3").exists()
    assert not (tmp_path / "backups" / f"{backups[0].id}.manifest.json").exists()
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 7


def test_backup_id_cannot_escape_backup_root(migrated_engine, tmp_path: Path) -> None:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    escape_id = r"..\escape"
    escape_db = tmp_path / "escape.sqlite3"
    with sqlite3.connect(escape_db) as connection:
        connection.execute("CREATE TABLE escaped (id INTEGER PRIMARY KEY)")
    escape_sha256 = _sha256_file(escape_db)
    (tmp_path / "escape.manifest.json").write_text(
        (
            '{"id":"..\\\\escape","schema_version":"truyenaudio-studio.backup.v1",'
            f'"database":"escape.sqlite3","sha256":"{escape_sha256[0]}",'
            f'"byte_size":{escape_sha256[1]},"integrity_check":"ok"}}'
        ),
        encoding="utf-8",
    )
    service = BackupService(
        db_path=Path(migrated_engine.url.database),
        backup_root=backup_root,
        artifact_root=tmp_path,
    )

    with pytest.raises(BackupVerificationError, match="invalid backup id"):
        service.verify(escape_id)


def _seed_artifact_row(engine, relative_path: str, sha256: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO artifacts
                (id, kind, status, relative_path, sha256, byte_size, mime_type,
                 input_hash, settings_hash, created_at, updated_at)
                VALUES
                ('018f0000-0000-7000-8000-000000009001',
                 'MASTER_MP3',
                 'READY',
                 :relative_path,
                 :sha256,
                 12,
                 'audio/mpeg',
                 :input_hash,
                 :settings_hash,
                 '2026-08-19T00:00:00+00:00',
                 '2026-08-19T00:00:00+00:00')
                """
            ),
            {
                "relative_path": relative_path,
                "sha256": sha256,
                "input_hash": HASH_B,
                "settings_hash": HASH_C,
            },
        )


def _sha256_bytes(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> tuple[str, int]:
    import hashlib

    sha256 = hashlib.sha256()
    byte_size = 0
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            byte_size += len(chunk)
            sha256.update(chunk)
    return sha256.hexdigest(), byte_size


class _IncrementingClock:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> str:
        self._value += 1
        return f"20260819T00000{self._value:02d}Z"
