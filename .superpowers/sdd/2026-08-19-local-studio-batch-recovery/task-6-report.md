# Task 6 Report: SQLite Backup/Restore, Disk Guard, Reviewable Cleanup

## Status

Implemented.

## Scope

- Added safe storage services under `backend/app/modules/storage/`.
- Added `/api/storage` router and registered it in `create_app`.
- Added `CleanupPreview` frontend component and test.
- Preserved Task 1-5 behavior; no migration, diagnostics, SSE, scraper, URL fetch, auto-upload, multi-voice, cloud TTS, paid network, or Task 7 scope was added.

## TDD Evidence

### RED 1: storage services

Command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/storage -q
```

Result:

```text
ERROR backend\tests\storage\test_backup_restore.py
ERROR backend\tests\storage\test_cleanup.py
ModuleNotFoundError: No module named 'app.modules.storage'
2 errors in 0.26s
```

### GREEN 1: storage services

Command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/storage -q
```

Result:

```text
7 passed in 2.57s
```

### RED 2: API and frontend registration

Commands:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/api/test_storage_api.py -q
npm test -- CleanupPreview.test.tsx --run
```

Results:

```text
assert 404 == 200
```

```text
Failed to resolve import "./CleanupPreview"
```

### GREEN 2: API and frontend registration

Commands:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/storage backend/tests/api/test_storage_api.py -q
npm test -- CleanupPreview.test.tsx --run
```

Results:

```text
8 passed in 3.19s
```

```text
1 passed
```

## Implementation Notes

- `BackupService.create()` uses `sqlite3.Connection.backup`, verifies `PRAGMA integrity_check`, writes a SHA-256 manifest, atomically renames database and manifest files, and keeps newest 7 verified backups.
- `BackupService.restore_to()` requires a held restore lock token, restores through a temp database, verifies integrity, scans artifact pointers against the artifact root and checksums, creates a pre-restore backup when replacing an existing DB, then swaps atomically.
- `DiskGuard.can_create()` implements warning at used `>=15GiB`, hard block when `used + estimate >=20GiB`, and hard block when `free - estimate <5GiB`.
- `CleanupService.preview()` is preview-first and only produces allowed candidate types: stale partials, superseded cache artifacts, unapproved previews, and stale WAVs when an MP3 master is approved.
- `CleanupService.execute()` requires the same plan snapshot hash, re-resolves each path, rechecks protected status and checksum, deletes only individual resolved candidate files, updates artifact status, and writes one audit event per deleted file.
- Protected cleanup exclusions include source snapshots, translation markdown, approved masters, evidence/license/consent artifacts, private archives, and publication bundles.

## Ruling

Ruling: restore lock semantics use an explicit `RestoreLockToken` that acquires the existing API and worker startup lock files before restore. This was necessary because the current app has startup locks but no in-process lifecycle API to stop and restart both API and worker safely from the storage module.

Cost if wrong: restore from the live API route will return a lock conflict while the API owns its startup lock, so an operator must stop API/worker and run restore in a stopped process context. This is safer than restoring under live writers and keeps Task 6 inside the existing lifecycle boundary.

## Verification

Commands run:

```powershell
.\.venv\Scripts\python -m ruff check backend/app/modules/storage backend/app/api/storage.py backend/app/main.py backend/tests/storage backend/tests/api/test_storage_api.py
.\.venv\Scripts\python -m pytest backend/tests/db/test_schema.py backend/tests/artifacts backend/tests/storage backend/tests/api/test_storage_api.py -q
npm run build
.\.venv\Scripts\python -m pytest backend/tests -q
npm test -- --run
```

Results:

```text
All checks passed!
48 passed, 1 warning in 9.25s
frontend build passed
336 passed, 1 warning in 66.58s
15 passed
```

Known warning:

```text
SAWarning in backend/tests/db/test_schema.py for existing cyclic FK sort during compare_metadata
```

## Files Changed

- `backend/app/modules/storage/__init__.py`
- `backend/app/modules/storage/backup.py`
- `backend/app/modules/storage/disk.py`
- `backend/app/modules/storage/cleanup.py`
- `backend/app/api/storage.py`
- `backend/app/main.py`
- `backend/tests/storage/test_backup_restore.py`
- `backend/tests/storage/test_cleanup.py`
- `backend/tests/api/test_storage_api.py`
- `frontend/src/features/storage/CleanupPreview.tsx`
- `frontend/src/features/storage/CleanupPreview.test.tsx`
