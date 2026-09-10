"""G-PERF Backup row: observable progress and real incremental artifact snapshots.

The benchmark fixture proves the API surface exists by reading the real signature; these
tests prove the behaviour behind it, on a real database and real artifact files:

- ``create(progress=...)`` reports every real milestone and finishes with exact totals;
- the second backup hardlinks artifacts the newest snapshot already holds with the same
  checksum instead of copying them again, and says so in its manifest;
- a pruned (deleted) base backup never takes the surviving backup with it;
- verification still catches a tampered or missing artifact;
- manifests written before these fields existed still verify.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.storage import create_storage_router
from app.db.base import create_engine_for
from app.modules.storage.backup import (
    BACKUP_PROGRESS_PHASES,
    BackupProgress,
    BackupProgressTracker,
    BackupService,
    BackupVerificationError,
)
from app.settings.config import Settings


class _TickingClock:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> str:
        self._value += 1
        return f"20260910T0100{self._value:02d}Z"


class _BlockingTracker(BackupProgressTracker):
    """Holds a running create() at one phase so the progress route can be read mid-flight."""

    def __init__(self, phase: str) -> None:
        super().__init__()
        self._phase = phase
        self.paused = threading.Event()
        self.release = threading.Event()

    def __call__(self, progress: BackupProgress) -> None:
        super().__call__(progress)
        if progress.phase == self._phase and not self.paused.is_set():
            self.paused.set()
            if not self.release.wait(timeout=30):
                raise AssertionError("progress route test timed out while holding create()")


def test_create_reports_progress_at_every_real_milestone(migrated_engine, tmp_path: Path) -> None:
    data_root, db_path = _data_root(migrated_engine, tmp_path)
    payloads = _artifact_payloads(5)
    _seed(data_root, db_path, payloads)
    service = _service(data_root, db_path)

    events: list[BackupProgress] = []
    view = service.create(progress=events.append)

    phases = [event.phase for event in events]
    assert len(events) >= 3
    assert phases[0] == "starting"
    assert phases[-1] == "completed"
    order = [BACKUP_PROGRESS_PHASES.index(phase) for phase in phases]
    assert order == sorted(order)
    assert {"starting", "database", "artifacts", "manifest", "verifying", "completed"} <= set(phases)

    # "starting" predates reading the database snapshot, so its totals are honestly 0;
    # every later milestone carries the real totals of this backup.
    assert events[0].total_artifacts == 0
    assert events[0].total_bytes == 0
    total_bytes = sum(len(payload) for payload in payloads.values())
    for event in events[1:]:
        assert event.total_artifacts == len(payloads)
        assert event.total_bytes == total_bytes

    final = events[-1]
    assert final.copied_artifacts == final.total_artifacts == len(payloads)
    assert final.copied_bytes == final.total_bytes == total_bytes

    artifact_events = [event for event in events if event.phase == "artifacts"]
    assert artifact_events[-1].copied_artifacts == len(payloads)
    assert [event.copied_artifacts for event in artifact_events] == sorted(
        event.copied_artifacts for event in artifact_events
    )

    database_event = next(event for event in events if event.phase == "database")
    assert database_event.copied_artifacts == 0
    assert database_event.copied_bytes == 0
    # "database" carries the real byte size of the file the online backup produced.
    assert str(view.path.stat().st_size) in (database_event.message or "")


def test_constructor_progress_callback_is_the_default_and_create_can_override_it(
    migrated_engine,
    tmp_path: Path,
) -> None:
    data_root, db_path = _data_root(migrated_engine, tmp_path)
    payloads = _artifact_payloads(2)
    _seed(data_root, db_path, payloads)
    default_events: list[BackupProgress] = []
    service = _service(data_root, db_path, progress_callback=default_events.append)

    service.create()
    assert default_events[-1].phase == "completed"
    routed = len(default_events)

    override: list[BackupProgress] = []
    service.create(progress=override.append)

    assert len(default_events) == routed  # the per-call hook wins
    assert override[-1].phase == "completed"


def test_a_raising_progress_callback_aborts_the_backup_and_leaves_no_partial_files(
    migrated_engine,
    tmp_path: Path,
) -> None:
    data_root, db_path = _data_root(migrated_engine, tmp_path)
    payloads = _artifact_payloads(4)
    _seed(data_root, db_path, payloads)
    service = _service(data_root, db_path)

    def explode(progress: BackupProgress) -> None:
        if progress.phase == "artifacts":
            raise RuntimeError("progress hook exploded")

    with pytest.raises(RuntimeError, match="progress hook exploded"):
        service.create(progress=explode)

    # Fail fast (no swallowed callback error) still cleans up like any other failure.
    assert sorted(path.name for path in service.backup_root.iterdir()) == []


def test_second_backup_hardlinks_unchanged_artifacts_instead_of_copying(
    migrated_engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root, db_path = _data_root(migrated_engine, tmp_path)
    payloads = _artifact_payloads(4)
    _seed(data_root, db_path, payloads)
    service = _service(data_root, db_path)

    first = service.create()
    first_manifest = _manifest(service, first.id)
    assert first_manifest["base_backup_id"] is None
    assert first_manifest["copied_artifact_count"] == len(payloads)
    assert first_manifest["linked_artifact_count"] == 0

    new_relative_path = "audio/track-new.mp3"
    new_payload = b"a brand new artifact" * 16
    _seed(data_root, db_path, {new_relative_path: new_payload})

    copied_sources: list[str] = []
    real_copyfile = shutil.copyfile

    def counting_copyfile(source, target, **kwargs):  # type: ignore[no-untyped-def]
        copied_sources.append(str(source))
        return real_copyfile(source, target, **kwargs)

    monkeypatch.setattr(shutil, "copyfile", counting_copyfile)

    second = service.create()
    second_manifest = _manifest(service, second.id)
    second_index = second_manifest["artifact_index"]

    assert second_manifest["base_backup_id"] == first.id
    assert second_manifest["linked_artifact_count"] == len(payloads)
    assert second_manifest["copied_artifact_count"] == 1
    # Only the new artifact was actually written to disk.
    assert len(copied_sources) == 1
    assert copied_sources[0].endswith("track-new.mp3")
    assert second_index[new_relative_path]["storage"] == "copied"
    assert all(second_index[relative_path]["storage"] == "linked" for relative_path in payloads)

    linked_path = _snapshot_file(service, second.id, second_index["audio/track-00.mp3"]["snapshot_path"])
    donor_path = _snapshot_file(service, first.id, first_manifest["artifact_index"]["audio/track-00.mp3"]["snapshot_path"])
    assert linked_path.read_bytes() == payloads["audio/track-00.mp3"]
    assert linked_path.stat().st_ino == donor_path.stat().st_ino
    assert linked_path.stat().st_nlink >= 2

    copied_path = _snapshot_file(service, second.id, second_index[new_relative_path]["snapshot_path"])
    live_path = data_root / "artifacts" / new_relative_path
    assert copied_path.read_bytes() == new_payload
    assert copied_path.stat().st_ino != live_path.stat().st_ino

    assert service.verify(first.id).ok
    assert service.verify(second.id).ok
    assert all(summary.verified for summary in service.list_backups())


def test_prune_keeps_the_surviving_backup_self_contained(migrated_engine, tmp_path: Path) -> None:
    data_root, db_path = _data_root(migrated_engine, tmp_path)
    payloads = _artifact_payloads(3)
    _seed(data_root, db_path, payloads)
    service = _service(data_root, db_path, retention=1)

    first = service.create()
    second = service.create()
    third = service.create()

    assert [path.name for path in service.backup_root.glob("*.manifest.json")] == [f"{third.id}.manifest.json"]
    assert not (service.backup_root / f"{first.id}.artifacts").exists()
    assert not (service.backup_root / f"{second.id}.artifacts").exists()

    manifest = _manifest(service, third.id)
    index = manifest["artifact_index"]
    assert manifest["base_backup_id"] == second.id
    assert manifest["linked_artifact_count"] == len(payloads)
    assert service.verify(third.id).ok

    for relative_path, payload in payloads.items():
        snapshot_path = _snapshot_file(service, third.id, index[relative_path]["snapshot_path"])
        assert snapshot_path.read_bytes() == payload
        assert hashlib.sha256(payload).hexdigest() == index[relative_path]["sha256"]
        # One link only: the data lives in this snapshot, not in a pruned backup.
        assert snapshot_path.stat().st_nlink == 1

    target = tmp_path / "restored-from-pruned-chain"
    with service.acquire_restore_locks() as token:
        restored = service.restore_copy(third.id, target, confirm_target=str(target), lock_token=token)

    assert restored.artifact_pointer_count == len(payloads)
    for relative_path, payload in payloads.items():
        assert (target / "artifacts" / relative_path).read_bytes() == payload


def test_a_damaged_base_snapshot_never_poisons_the_next_backup(migrated_engine, tmp_path: Path) -> None:
    data_root, db_path = _data_root(migrated_engine, tmp_path)
    payloads = _artifact_payloads(3)
    _seed(data_root, db_path, payloads)
    service = _service(data_root, db_path)

    first = service.create()
    first_manifest = _manifest(service, first.id)
    damaged = _snapshot_file(
        service,
        first.id,
        first_manifest["artifact_index"]["audio/track-00.mp3"]["snapshot_path"],
    )
    damaged.write_bytes(b"damaged base artifact")

    second = service.create()
    second_manifest = _manifest(service, second.id)
    second_index = second_manifest["artifact_index"]

    assert second_manifest["base_backup_id"] == first.id
    # The damaged inode was rejected and only that artifact was rebuilt from the live
    # source; the intact ones stay hardlinked to the base snapshot.
    assert second_manifest["linked_artifact_count"] == len(payloads) - 1
    assert second_manifest["copied_artifact_count"] == 1
    assert second_index["audio/track-00.mp3"]["storage"] == "copied"
    assert second_index["audio/track-01.mp3"]["storage"] == "linked"
    assert service.verify(second.id).ok
    repaired = _snapshot_file(service, second.id, second_index["audio/track-00.mp3"]["snapshot_path"])
    assert repaired.read_bytes() == payloads["audio/track-00.mp3"]
    assert repaired.stat().st_ino != damaged.stat().st_ino


def test_incremental_disabled_copies_every_artifact(migrated_engine, tmp_path: Path) -> None:
    data_root, db_path = _data_root(migrated_engine, tmp_path)
    payloads = _artifact_payloads(2)
    _seed(data_root, db_path, payloads)
    service = _service(data_root, db_path, incremental=False)

    first = service.create()
    second = service.create()
    second_manifest = _manifest(service, second.id)

    assert second_manifest["base_backup_id"] is None
    assert second_manifest["linked_artifact_count"] == 0
    assert second_manifest["copied_artifact_count"] == len(payloads)
    assert service.verify(first.id).ok
    assert service.verify(second.id).ok


def test_verify_still_detects_tampered_and_missing_artifacts(migrated_engine, tmp_path: Path) -> None:
    data_root, db_path = _data_root(migrated_engine, tmp_path)
    payloads = _artifact_payloads(2)
    _seed(data_root, db_path, payloads)
    service = _service(data_root, db_path)

    view = service.create()
    index = _manifest(service, view.id)["artifact_index"]
    tampered = _snapshot_file(service, view.id, index["audio/track-00.mp3"]["snapshot_path"])
    original = tampered.read_bytes()

    tampered.write_bytes(b"\xff" + original[1:])
    with pytest.raises(BackupVerificationError, match="checksum mismatch"):
        service.verify(view.id)
    assert service.list_backups()[0].verified is False

    tampered.write_bytes(original)
    assert service.verify(view.id).ok

    missing = _snapshot_file(service, view.id, index["audio/track-01.mp3"]["snapshot_path"])
    missing.unlink()
    with pytest.raises(BackupVerificationError, match="artifact pointer missing"):
        service.verify(view.id)


def test_manifests_from_before_the_incremental_fields_still_verify(migrated_engine, tmp_path: Path) -> None:
    data_root, db_path = _data_root(migrated_engine, tmp_path)
    payloads = _artifact_payloads(2)
    _seed(data_root, db_path, payloads)
    service = _service(data_root, db_path)

    view = service.create()
    manifest_path = service.backup_root / f"{view.id}.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    legacy_keys = {
        "id",
        "schema_version",
        "database",
        "sha256",
        "byte_size",
        "integrity_check",
        "artifact_snapshot",
        "artifact_count",
        "created_at",
    }
    legacy = {key: value for key, value in manifest.items() if key in legacy_keys}
    assert "artifact_index" not in legacy
    manifest_path.write_text(json.dumps(legacy, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    verification = service.verify(view.id)
    assert verification.ok
    assert verification.sha256 == view.sha256
    assert verification.byte_size == view.byte_size

    # A manifest without an artifact index cannot donate links; the next backup copies.
    second = service.create()
    second_manifest = _manifest(service, second.id)
    assert second_manifest["base_backup_id"] is None
    assert second_manifest["linked_artifact_count"] == 0
    assert second_manifest["copied_artifact_count"] == len(payloads)
    assert service.verify(second.id).ok


def test_progress_route_reports_the_running_backup_and_204_when_idle(
    migrated_engine,
    tmp_path: Path,
) -> None:
    data_root, db_path = _data_root(migrated_engine, tmp_path)
    payloads = _artifact_payloads(6)
    _seed(data_root, db_path, payloads)
    tracker = _BlockingTracker("database")
    app = FastAPI()
    app.include_router(create_storage_router(Settings(data_root=data_root), progress_tracker=tracker))

    with TestClient(app) as client:
        assert client.get("/api/storage/backups/progress").status_code == 204
        result: dict[str, object] = {}

        def post_backup() -> None:
            response = client.post("/api/storage/backups")
            result["status"] = response.status_code
            result["body"] = response.json()

        thread = threading.Thread(target=post_backup)
        thread.start()
        try:
            assert tracker.paused.wait(timeout=30), "create() never reached the database milestone"
            running = client.get("/api/storage/backups/progress")
            assert running.status_code == 200
            body = running.json()
            assert body["phase"] == "database"
            assert body["totalArtifacts"] == len(payloads)
            assert body["totalBytes"] == sum(len(payload) for payload in payloads.values())
            assert body["copiedArtifacts"] == 0
            assert body["copiedBytes"] == 0
        finally:
            tracker.release.set()
            thread.join(timeout=60)

        assert not thread.is_alive()
        assert result["status"] == 200
        assert client.get("/api/storage/backups/progress").status_code == 204

        completed = service_manifest_count(data_root)
        assert completed == 1


def _data_root(migrated_engine, tmp_path: Path) -> tuple[Path, Path]:
    data_root = tmp_path / "data"
    data_root.mkdir()
    db_path = data_root / "studio.sqlite3"
    with sqlite3.connect(Path(migrated_engine.url.database)) as source, sqlite3.connect(db_path) as target:
        source.backup(target)
    return data_root, db_path


def _service(data_root: Path, db_path: Path, *, retention: int = 7, **kwargs: object) -> BackupService:
    return BackupService(
        db_path=db_path,
        backup_root=data_root / "backups",
        artifact_root=data_root,
        retention_count=retention,
        api_lock_path=data_root / "studio-api.lock",
        worker_lock_path=data_root / "studio-worker.lock",
        clock=_TickingClock(),
        **kwargs,
    )


def _artifact_payloads(count: int, size: int = 4096) -> dict[str, bytes]:
    return {f"audio/track-{index:02d}.mp3": bytes([index + 1]) * size for index in range(count)}


def _seed(data_root: Path, db_path: Path, payloads: dict[str, bytes]) -> None:
    for relative_path, payload in payloads.items():
        path = data_root / "artifacts" / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    engine = create_engine_for(db_path)
    try:
        with engine.begin() as connection:
            for relative_path, payload in payloads.items():
                connection.execute(
                    text(
                        """
                        INSERT INTO artifacts
                        (id, kind, status, relative_path, sha256, byte_size, mime_type,
                         input_hash, settings_hash, created_at, updated_at)
                        VALUES
                        (:id, 'MASTER_MP3', 'READY', :relative_path, :sha256, :byte_size, 'audio/mpeg',
                         :input_hash, :settings_hash,
                         '2026-09-10T00:00:00+00:00', '2026-09-10T00:00:00+00:00')
                        """
                    ),
                    {
                        "id": _artifact_id(relative_path),
                        "relative_path": relative_path,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "byte_size": len(payload),
                        "input_hash": hashlib.sha256(b"input:" + relative_path.encode()).hexdigest(),
                        "settings_hash": hashlib.sha256(b"settings:" + relative_path.encode()).hexdigest(),
                    },
                )
    finally:
        engine.dispose()


def _artifact_id(relative_path: str) -> str:
    return f"018f0000-0000-7000-8000-{int(hashlib.sha256(relative_path.encode()).hexdigest()[:12], 16):012d}"


def _manifest(service: BackupService, backup_id: str) -> dict:
    return json.loads((service.backup_root / f"{backup_id}.manifest.json").read_text(encoding="utf-8"))


def _snapshot_file(service: BackupService, backup_id: str, snapshot_relative_path: str) -> Path:
    return service.backup_root / f"{backup_id}.artifacts" / snapshot_relative_path


def service_manifest_count(data_root: Path) -> int:
    return len(list((data_root / "backups").glob("*.manifest.json")))
