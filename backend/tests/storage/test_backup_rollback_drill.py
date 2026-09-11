"""R01 backup/rollback drill: back up the legacy data root, upgrade, then roll back.

This is the drill behind the release-gate claim "rollback restore đúng checksum". It
runs only local code - sqlite online backup, alembic and the real BackupService. No
cloud call, no model download, no touch of the studio data root.

Sequence:

1. build a real data root at revision 0016 (project/chapters/source/translation + a
   READY master artifact on disk);
2. take a verified backup and record the sha256/byte_size the manifest recorded plus
   the *content* digest of the database;
3. upgrade the live database to head and add post-upgrade rows, so a wrong restore
   would be visible;
4. restore the backup into a NEW, isolated data root;
5. prove the restored database is byte-for-byte the pre-upgrade *content* (logical
   digest, row counts, revision 0016), that the artifact came back with its checksum,
   and that the post-upgrade rows are gone;
6. prove the corrupted-backup path fails closed, and that the restored root is
   correctly reported as NOT at head until it is upgraded again.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.contracts import JobKind, JobStatus, RunStatus
from app.db.base import create_engine_for, session_factory
from app.db.migration_status import MIGRATION_INCOMPLETE, migration_status
from app.db.models import (
    Job,
    Project,
    TranslationRun,
    TranslationSegment,
    VoicePreviewJob,
    VoicePreviewTextKind,
)
from app.modules.storage.backup import (
    BACKUP_PROGRESS_PHASES,
    BackupError,
    BackupProgress,
    BackupService,
    BackupVerificationError,
)
from tests.fixtures.legacy_data_root import (
    LEGACY_REVISION,
    PROJECT_ID,
    build_legacy_data_root,
    integrity,
    row_counts,
    run_id,
    sha256_bytes,
    table_names,
    sqlite_content_sha256,
    upgrade,
)


SUMMARY_JOB_ID = "88888888-8888-7888-8888-888888888888"
PREVIEW_JOB_ID = "99999999-9999-7999-8999-999999999999"


def _service(data_root: Path) -> BackupService:
    """Same wiring app.api.storage uses: artifacts live under the data root."""
    return BackupService(
        db_path=data_root / "studio.sqlite3",
        backup_root=data_root / "backups",
        artifact_root=data_root,
        api_lock_path=data_root / "studio-api.lock",
        worker_lock_path=data_root / "studio-worker.lock",
    )


def test_backup_then_upgrade_then_restore_rolls_back_to_the_verified_backup(tmp_path: Path) -> None:
    seed = build_legacy_data_root(tmp_path / "live-data")
    service = _service(seed.data_root)
    phases: list[BackupProgress] = []

    backup = service.create(progress=phases.append)

    # --- evidence 1: the backup is self-consistent and its progress is observable ---
    verification = service.verify(backup.id)
    assert verification.ok is True
    assert verification.integrity_check == "ok"
    assert verification.sha256 == backup.sha256
    assert verification.byte_size == backup.byte_size
    seen = [progress.phase for progress in phases]
    assert seen == sorted(seen, key=BACKUP_PROGRESS_PHASES.index)
    assert seen[0] == "starting" and seen[-1] == "completed"
    assert {"database", "artifacts", "manifest", "verifying"} <= set(seen)
    assert [summary.verified for summary in service.list_backups()] == [True]

    pre_upgrade_content = sqlite_content_sha256(seed.db_path)
    pre_upgrade_counts = row_counts(seed.db_path)
    pre_upgrade_artifact_sha = sha256_bytes(seed.artifact_file.read_bytes())

    # --- evidence 2: upgrade to head, then write rows a wrong restore would keep ---
    upgrade(seed.db_path, "head")
    engine = create_engine_for(seed.db_path)
    try:
        with session_factory(engine)() as session:
            session.add(
                Job(
                    id=SUMMARY_JOB_ID,
                    kind=JobKind.SUMMARIZE.value,
                    status=JobStatus.QUEUED.value,
                    project_id=PROJECT_ID,
                    idempotency_key="post-upgrade-summarize",
                )
            )
            session.add(
                VoicePreviewJob(
                    id=PREVIEW_JOB_ID,
                    preset_id="fake-vi-narrator",
                    text_kind=VoicePreviewTextKind.SAMPLE.value,
                    text_sha256=sha256_bytes(b"preview"),
                    text="preview",
                    cache_key=sha256_bytes(b"preview-cache"),
                    status="QUEUED",
                )
            )
            session.commit()
    finally:
        engine.dispose()
    assert migration_status(seed.db_path).in_sync

    # --- evidence 3: restore into a NEW isolated data root ---
    restored_root = tmp_path / "restored-data"
    with service.acquire_restore_locks() as token:
        restored = service.restore_to_data_root(backup.id, restored_root, lock_token=token)

    assert restored.integrity_check == "ok"
    assert restored.artifact_pointer_count == pre_upgrade_counts["artifacts"] > 0
    restored_db = restored_root / "studio.sqlite3"
    assert restored.target_path == restored_db

    # checksum/integrity match: the backup itself is unchanged and still verifies,
    # and the restored database reproduces the pre-upgrade content exactly.
    re_verified = service.verify(backup.id)
    assert re_verified.sha256 == verification.sha256
    assert integrity(restored_db) == "ok"
    assert sqlite_content_sha256(restored_db) == pre_upgrade_content
    assert row_counts(restored_db) == pre_upgrade_counts

    # the rollback target really is the pre-upgrade revision...
    from app.db.migration_status import head_revision, read_database_revision

    assert read_database_revision(restored_db) == LEGACY_REVISION
    assert migration_status(restored_db).detail == MIGRATION_INCOMPLETE

    # ...and the post-upgrade rows are gone, proving this is not a partial copy. The
    # additive tables (0017 voice preview jobs, 0019 memory_indexes) do not merely sit
    # empty in the rollback target: they do not exist, which is exactly the
    # pre-upgrade schema.
    restored_tables = table_names(restored_db)
    assert "voice_preview_jobs" not in restored_tables
    assert "memory_indexes" not in restored_tables
    assert "voice_preview_jobs" in table_names(seed.db_path)
    assert "memory_indexes" in table_names(seed.db_path)
    restored_engine = create_engine_for(restored_db)
    try:
        with session_factory(restored_engine)() as session:
            assert session.get(Job, SUMMARY_JOB_ID) is None
            project = session.get(Project, PROJECT_ID)
            assert project is not None
            run = session.get(TranslationRun, run_id(1))
            assert run is not None and run.status == RunStatus.APPROVED.value
            targets = session.query(TranslationSegment).filter_by(translation_run_id=run.id).all()
            assert len(targets) == 2
            assert session.scalar(select(func.count()).select_from(Job)) == 0
    finally:
        restored_engine.dispose()

    # artifacts came back with their checksum, under the restored root only.
    restored_artifact = restored_root / "artifacts" / seed.artifact_relative_path
    assert restored_artifact.read_bytes() == seed.artifact_bytes
    assert sha256_bytes(restored_artifact.read_bytes()) == pre_upgrade_artifact_sha
    assert seed.artifact_file.read_bytes() == seed.artifact_bytes  # live root untouched

    # the restored (older-schema) root can be upgraded again, back to head - which is
    # whatever the chain head is now, not the frozen legacy anchor.
    upgrade(restored_db, "head")
    assert migration_status(restored_db).database_revision == head_revision()
    assert row_counts(restored_db) == pre_upgrade_counts

    print(
        f"EVIDENCE backup-rollback backup={backup.id} sha256={verification.sha256[:16]}... "
        f"byte_size={verification.byte_size} integrity={verification.integrity_check} "
        f"content_digest_match=True rows_before={pre_upgrade_counts} "
        f"restored_revision={LEGACY_REVISION} then_reupgraded={head_revision()} "
        f"artifact_sha256_match=True phases={seen}"
    )


def test_restore_refuses_a_corrupted_backup_and_a_used_target(tmp_path: Path) -> None:
    seed = build_legacy_data_root(tmp_path / "guard-data")
    service = _service(seed.data_root)
    backup = service.create()

    corrupted_root = tmp_path / "corrupted-data"
    with service.acquire_restore_locks() as token:
        first = service.restore_to_data_root(backup.id, corrupted_root, lock_token=token)
    assert first.integrity_check == "ok"

    with service.acquire_restore_locks() as token:
        with pytest.raises(BackupError, match="already exists"):
            service.restore_to_data_root(backup.id, corrupted_root, lock_token=token)

    # A truncated backup file must fail verification instead of being restored.
    backup_path = service.backup_root / f"{backup.id}.sqlite3"
    backup_path.write_bytes(backup_path.read_bytes()[: 4096])
    with pytest.raises(BackupVerificationError):
        service.verify(backup.id)
    with service.acquire_restore_locks() as token:
        with pytest.raises(BackupVerificationError):
            service.restore_to_data_root(backup.id, tmp_path / "never-data", lock_token=token)
    assert not (tmp_path / "never-data").exists()

    print("EVIDENCE restore-guards used_target_rejected=True corrupted_backup_rejected=True")
