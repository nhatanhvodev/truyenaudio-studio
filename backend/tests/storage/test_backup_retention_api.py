"""U10 round 1: storage negative paths, retention preview and copy restore.

Acceptance covered here (implementation-plan.md U10):

- a retention change is only a *plan*: no backup file is deleted by reading or
  writing the retention setting (deletion stays an explicit cleanup action);
- a restore into a copy targets a real isolated data root, requires the caller to
  repeat that path as confirmation, refuses an existing/overlapping target and
  verifies the backup checksum before writing anything;
- invalid inputs are rejected with precise codes instead of being clamped.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.storage import create_storage_router
from app.db.base import create_engine_for
from app.modules.storage.backup import (
    BackupError,
    BackupService,
    BackupVerificationError,
    RestoreConfirmationRequired,
    RetentionCountInvalid,
)
from app.settings.config import Settings

HASH_B = "b" * 64
HASH_C = "c" * 64


def _seed_artifact_row(engine, relative_path: str, sha256: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO artifacts
                (id, kind, status, relative_path, sha256, byte_size, mime_type,
                 input_hash, settings_hash, created_at, updated_at)
                VALUES
                (:id, 'MASTER_MP3', 'READY', :relative_path, :sha256, 12, 'audio/mpeg',
                 :input_hash, :settings_hash,
                 '2026-08-19T00:00:00+00:00', '2026-08-19T00:00:00+00:00')
                """
            ),
            {
                "id": f"018f0000-0000-7000-8000-00000001{abs(hash(relative_path)) % 10000:04d}",
                "relative_path": relative_path,
                "sha256": sha256,
                "input_hash": HASH_B,
                "settings_hash": HASH_C,
            },
        )


class _TickingClock:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> str:
        self._value += 1
        return f"20260819T0100{self._value:02d}Z"


def _service(tmp_path: Path, db_path: Path, repo_root: Path) -> BackupService:
    return BackupService(
        db_path=db_path,
        backup_root=repo_root / "backups",
        artifact_root=repo_root,
        api_lock_path=repo_root / "studio-api.lock",
        worker_lock_path=repo_root / "studio-worker.lock",
        clock=_TickingClock(),
    )


def _data_root_with_backups(migrated_engine, tmp_path: Path, count: int) -> tuple[Path, BackupService]:
    data_root = tmp_path / "data"
    data_root.mkdir()
    db_path = data_root / "studio.sqlite3"
    with sqlite3.connect(Path(migrated_engine.url.database)) as source, sqlite3.connect(db_path) as target:
        source.backup(target)
    engine = create_engine_for(db_path)
    payload = b"artifact payload"
    artifact_path = data_root / "artifacts" / "audio" / "one.mp3"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(payload)
    _seed_artifact_row(engine, "audio/one.mp3", hashlib.sha256(payload).hexdigest())
    engine.dispose()

    service = _service(tmp_path, db_path, data_root)
    for _ in range(count):
        service.create()
    return data_root, service


def test_list_backups_reports_verification_state(migrated_engine, tmp_path: Path) -> None:
    _, service = _data_root_with_backups(migrated_engine, tmp_path, 2)

    backups = service.list_backups()

    assert len(backups) == 2
    assert all(item.verified for item in backups)
    assert all(item.sha256 for item in backups)

    # Corrupting the newest backup makes it visible as unverified instead of hidden.
    newest = backups[0].id
    (service.backup_root / f"{newest}.sqlite3").write_bytes(b"corrupted")

    after = service.list_backups()
    broken = next(item for item in after if item.id == newest)
    assert broken.verified is False
    assert broken.sha256 == ""
    assert broken.verification_error


def test_retention_plan_never_deletes_and_rejects_invalid_counts(migrated_engine, tmp_path: Path) -> None:
    _, service = _data_root_with_backups(migrated_engine, tmp_path, 3)
    before = sorted(path.name for path in service.backup_root.iterdir())

    plan = service.retention_plan(1)

    assert plan.applied is False
    assert plan.requires_confirmation is True
    assert plan.current_count == 3
    assert plan.requested_count == 1
    assert len(plan.kept) == 1
    assert len(plan.deletable) == 2
    assert plan.prune_on_create is True
    # Reading/writing the retention setting must not delete a single file.
    assert sorted(path.name for path in service.backup_root.iterdir()) == before

    no_op = service.retention_plan(3)
    assert no_op.deletable == ()
    assert no_op.requires_confirmation is False

    with pytest.raises(RetentionCountInvalid) as invalid:
        service.retention_plan(0)
    assert str(invalid.value) == "RETENTION_COUNT_INVALID"

    with pytest.raises(RetentionCountInvalid):
        service.retention_plan(True)  # type: ignore[arg-type]


def test_restore_copy_requires_confirmation_and_isolated_target(migrated_engine, tmp_path: Path) -> None:
    data_root, service = _data_root_with_backups(migrated_engine, tmp_path, 1)
    backup_id = service.list_backups()[0].id
    target = tmp_path / "restored-data"

    with pytest.raises(RestoreConfirmationRequired) as unconfirmed:
        service.restore_copy(backup_id, target, confirm_target=None, lock_token=None)
    assert str(unconfirmed.value) == "RESTORE_TARGET_CONFIRMATION_REQUIRED"

    with pytest.raises(RestoreConfirmationRequired):
        service.restore_copy(backup_id, target, confirm_target=str(tmp_path / "somewhere-else"), lock_token=None)

    with service.acquire_restore_locks() as token:
        restored = service.restore_copy(backup_id, target, confirm_target=str(target), lock_token=token)

    assert restored.target_path == target / "studio.sqlite3"
    assert restored.integrity_check == "ok"
    assert (target / "artifacts" / "audio" / "one.mp3").read_bytes() == b"artifact payload"
    # The source data root is untouched by a copy restore.
    assert (data_root / "artifacts" / "audio" / "one.mp3").read_bytes() == b"artifact payload"

    # A second restore into the same (now existing) target is refused.
    with service.acquire_restore_locks() as token:
        with pytest.raises(BackupError):
            service.restore_copy(backup_id, target, confirm_target=str(target), lock_token=token)

    # Restoring into the source data root is refused (must not overlap).
    with service.acquire_restore_locks() as token:
        with pytest.raises(BackupError):
            service.restore_copy(backup_id, data_root, confirm_target=str(data_root), lock_token=token)


def test_restore_copy_refuses_a_corrupted_backup(migrated_engine, tmp_path: Path) -> None:
    _, service = _data_root_with_backups(migrated_engine, tmp_path, 1)
    backup_id = service.list_backups()[0].id
    (service.backup_root / f"{backup_id}.sqlite3").write_bytes(b"not a sqlite file")
    target = tmp_path / "restored-corrupt"

    with service.acquire_restore_locks() as token:
        with pytest.raises(BackupVerificationError):
            service.restore_copy(backup_id, target, confirm_target=str(target), lock_token=token)

    assert not target.exists()


def test_storage_api_exposes_backups_retention_and_copy_restore(migrated_engine, tmp_path: Path) -> None:
    data_root, service = _data_root_with_backups(migrated_engine, tmp_path, 2)
    backup_id = service.list_backups()[0].id
    app = FastAPI()
    app.include_router(create_storage_router(Settings(data_root=data_root)))

    with TestClient(app) as client:
        listed = client.get("/api/storage/backups")
        assert listed.status_code == 200
        backups = listed.json()["backups"]
        assert len(backups) == 2
        assert backups[0]["verified"] is True
        assert "sha256" in backups[0]

        plan = client.get("/api/storage/retention", params={"count": 1})
        assert plan.status_code == 200
        body = plan.json()
        assert body["applied"] is False
        assert body["requiresConfirmation"] is True
        assert len(body["deletable"]) == 1
        # 2 backups = 2 database files + 2 manifests (artifact snapshot dirs are extra).
        stored = [
            path
            for path in data_root.joinpath("backups").iterdir()
            if path.suffix in {".sqlite3", ".json"}
        ]
        assert len(stored) == 4

        put = client.put("/api/storage/retention", json={"count": 5})
        assert put.status_code == 200
        assert put.json()["deletable"] == []

        invalid = client.put("/api/storage/retention", json={"count": 0})
        assert invalid.status_code == 400
        assert invalid.json()["detail"] == "RETENTION_COUNT_INVALID"

        target = tmp_path / "api-restored"
        restored = client.post(
            f"/api/storage/backups/{backup_id}/restore-copy",
            json={"targetDataRoot": str(target), "confirmTarget": str(target)},
        )
        assert restored.status_code == 200
        payload = restored.json()
        assert payload["integrityCheck"] == "ok"
        assert payload["artifactPointerCount"] == 1
        assert (target / "studio.sqlite3").is_file()
        assert (target / "artifacts" / "audio" / "one.mp3").is_file()

        # The source data root is untouched, and the same target cannot be reused.
        assert (data_root / "studio.sqlite3").is_file()
        again = client.post(
            f"/api/storage/backups/{backup_id}/restore-copy",
            json={"targetDataRoot": str(target), "confirmTarget": str(target)},
        )
        assert again.status_code == 409

        unconfirmed_target = tmp_path / "api-restored-2"
        unconfirmed = client.post(
            f"/api/storage/backups/{backup_id}/restore-copy",
            json={"targetDataRoot": str(unconfirmed_target), "confirmTarget": str(tmp_path / "other")},
        )
        assert unconfirmed.status_code == 400
        assert unconfirmed.json()["detail"] == "RESTORE_TARGET_CONFIRMATION_REQUIRED"
        assert not unconfirmed_target.exists()
