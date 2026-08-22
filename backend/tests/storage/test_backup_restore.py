from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import text

from app.modules.storage.backup import BackupService, RestoreLockRequired


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


class _IncrementingClock:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> str:
        self._value += 1
        return f"20260819T00000{self._value:02d}Z"
