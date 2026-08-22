from __future__ import annotations

from collections import namedtuple
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text

from app.modules.storage.cleanup import CleanupPlanStale, CleanupService
from app.modules.storage.disk import DiskGuard


HASH_B = "b" * 64
HASH_C = "c" * 64
NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
DiskUsage = namedtuple("DiskUsage", "total used free")


def test_cleanup_never_includes_protected_artifacts(db_session, artifact_store) -> None:
    protected_paths = {
        "source/chapter-1.txt",
        "translation/approved.md",
        "audio/approved.wav",
        "evidence/license.pdf",
        "exports/publication.zip",
    }
    for index, (kind, relative_path) in enumerate(
        [
            ("SOURCE_SNAPSHOT", "source/chapter-1.txt"),
            ("TRANSLATION_MARKDOWN", "translation/approved.md"),
            ("MASTER_WAV", "audio/approved.wav"),
            ("RIGHTS_EVIDENCE", "evidence/license.pdf"),
            ("PUBLICATION_BUNDLE", "exports/publication.zip"),
        ],
        start=1,
    ):
        _write_artifact(artifact_store.resolve(relative_path), f"protected {index}".encode())
        _insert_artifact(
            db_session,
            artifact_id=f"018f0000-0000-7000-8000-00000001000{index}",
            kind=kind,
            status="SUPERSEDED",
            relative_path=relative_path,
            payload=f"protected {index}".encode(),
        )
    db_session.commit()

    plan = CleanupService(db_session, artifact_store, clock=lambda: NOW).preview()

    assert protected_paths.isdisjoint({candidate.relative_path for candidate in plan.candidates})


def test_cleanup_execute_requires_same_snapshot_and_does_not_delete_stale_candidates(db_session, artifact_store) -> None:
    relative_path = "cache/old-preview.wav"
    path = artifact_store.resolve(relative_path)
    _write_artifact(path, b"old preview")
    _insert_artifact(
        db_session,
        artifact_id="018f0000-0000-7000-8000-000000020001",
        kind="VOICE_PREVIEW",
        status="READY",
        relative_path=relative_path,
        payload=b"old preview",
    )
    db_session.commit()
    service = CleanupService(db_session, artifact_store, clock=lambda: NOW)
    plan = service.preview()

    path.write_bytes(b"changed after preview")

    result = service.execute(plan.plan_id, plan.snapshot_hash)

    assert path.read_bytes() == b"changed after preview"
    assert result.deleted_count == 0
    assert result.skipped_count == 1
    assert db_session.execute(text("SELECT COUNT(*) FROM audit_events")).scalar_one() == 0
    with pytest.raises(CleanupPlanStale):
        service.execute(plan.plan_id, "0" * 64)


def test_cleanup_deletes_reviewed_candidates_and_reports_exact_freed_bytes(db_session, artifact_store) -> None:
    preview_path = artifact_store.resolve("previews/unapproved.wav")
    partial_path = artifact_store.resolve("audio/.segment.wav.partial")
    _write_artifact(preview_path, b"preview")
    _write_artifact(partial_path, b"partial")
    stale_time = NOW - timedelta(hours=25)
    _set_mtime(partial_path, stale_time)
    _insert_artifact(
        db_session,
        artifact_id="018f0000-0000-7000-8000-000000030001",
        kind="VOICE_PREVIEW",
        status="READY",
        relative_path="previews/unapproved.wav",
        payload=b"preview",
    )
    db_session.commit()
    service = CleanupService(db_session, artifact_store, clock=lambda: NOW)

    plan = service.preview()
    result = service.execute(plan.plan_id, plan.snapshot_hash)

    assert result.deleted_count == 2
    assert result.freed_bytes == len(b"preview") + len(b"partial")
    assert not preview_path.exists()
    assert not partial_path.exists()
    assert db_session.execute(text("SELECT COUNT(*) FROM audit_events")).scalar_one() == 2


def test_cleanup_mid_plan_unlink_failure_leaves_no_deleted_file_without_audit_status(
    db_session,
    artifact_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_path = artifact_store.resolve("previews/first.wav")
    second_path = artifact_store.resolve("previews/second.wav")
    _write_artifact(first_path, b"first")
    _write_artifact(second_path, b"second")
    _insert_artifact(
        db_session,
        artifact_id="018f0000-0000-7000-8000-000000040001",
        kind="VOICE_PREVIEW",
        status="READY",
        relative_path="previews/first.wav",
        payload=b"first",
    )
    _insert_artifact(
        db_session,
        artifact_id="018f0000-0000-7000-8000-000000040002",
        kind="VOICE_PREVIEW",
        status="READY",
        relative_path="previews/second.wav",
        payload=b"second",
    )
    db_session.commit()
    service = CleanupService(db_session, artifact_store, clock=lambda: NOW)
    plan = service.preview()
    original_replace = Path.replace

    def fail_second_replace(path: Path, target: Path) -> Path:
        if path == second_path:
            raise PermissionError("simulated second delete failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_second_replace)

    with pytest.raises(PermissionError, match="simulated second delete failure"):
        service.execute(plan.plan_id, plan.snapshot_hash)
    db_session.rollback()

    assert not first_path.exists()
    first_status = db_session.execute(
        text("SELECT status FROM artifacts WHERE id = '018f0000-0000-7000-8000-000000040001'")
    ).scalar_one()
    first_audits = db_session.execute(
        text("SELECT COUNT(*) FROM audit_events WHERE entity_id = '018f0000-0000-7000-8000-000000040001'")
    ).scalar_one()
    assert first_status == "DELETED"
    assert first_audits == 1
    assert second_path.exists()


def test_disk_guard_warns_at_15_gib_and_hard_blocks_at_20_gib_or_low_free(tmp_path: Path) -> None:
    gib = 1024**3
    warning = DiskGuard(tmp_path, usage_provider=lambda _path: DiskUsage(30 * gib, 15 * gib, 15 * gib))
    hard_used = DiskGuard(tmp_path, usage_provider=lambda _path: DiskUsage(30 * gib, 19 * gib, 11 * gib))
    hard_free = DiskGuard(tmp_path, usage_provider=lambda _path: DiskUsage(30 * gib, 10 * gib, 6 * gib))

    assert warning.can_create(1).level == "warning"
    assert not hard_used.can_create(1 * gib).allowed
    assert not hard_free.can_create(2 * gib).allowed


def _insert_artifact(
    session,
    *,
    artifact_id: str,
    kind: str,
    status: str,
    relative_path: str,
    payload: bytes,
) -> None:
    session.execute(
        text(
            """
            INSERT INTO artifacts
            (id, kind, status, relative_path, sha256, byte_size, mime_type,
             input_hash, settings_hash, created_at, updated_at)
            VALUES
            (:id, :kind, :status, :relative_path, :sha256, :byte_size, 'application/octet-stream',
             :input_hash, :settings_hash,
             '2026-08-10T00:00:00+00:00',
             '2026-08-10T00:00:00+00:00')
            """
        ),
        {
            "id": artifact_id,
            "kind": kind,
            "status": status,
            "relative_path": relative_path,
            "sha256": _sha256_bytes(payload),
            "byte_size": len(payload),
            "input_hash": _sha256_bytes(artifact_id.encode("ascii")),
            "settings_hash": _sha256_bytes(relative_path.encode("utf-8")),
        },
    )


def _write_artifact(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _set_mtime(path: Path, value: datetime) -> None:
    timestamp = value.timestamp()
    path.touch()
    import os

    os.utime(path, (timestamp, timestamp))


def _sha256_bytes(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()
