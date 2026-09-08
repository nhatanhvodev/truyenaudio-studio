# Baseline F01 — 08/09/2026

Repository: `D:\truyenaudio-studio\.worktrees\upgrade-code`
Base commit before F01: `c159d6a0f5863a4fc79ba966b80f50846fcff15e`

## Runtime and dependency snapshot

- Python: `3.12.13` from `D:\truyenaudio-studio\.venv\Scripts\python.exe`.
- Node.js: `v24.11.1`; npm: `11.6.2`.
- Backend dependency declarations: `backend/pyproject.toml` pins the FastAPI, Pydantic, SQLAlchemy, Alembic, pytest and related package versions; no dependency was changed by F01.
- Frontend dependency declarations: `frontend/package.json` pins React 19, Vite 7, Vitest and Playwright; no dependency was changed by F01.

## Validation results

| Check | Status | Command / evidence | Result |
| --- | --- | --- | --- |
| TDD RED: isolated restore seam | PASS | `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests/storage/test_backup_restore.py::test_backup_restores_database_and_artifacts_to_an_isolated_data_root -q` | Exit 1 before implementation: `AttributeError` because `restore_to_data_root` did not exist. This is the intended RED result. |
| TDD GREEN: storage/API focused suite | PASS | `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests/storage/test_backup_restore.py backend/tests/api/test_storage_api.py -q` | Exit 0; `11 passed` in 5.07s. |
| Backend baseline | PASS | `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` | Exit 0; `385 passed, 1 warning` in 74.25s. Warning: SQLAlchemy cannot topologically sort the existing FK cycle among `artifacts`, `chapters`, `exports`, `projects`, `source_revisions`, `translation_runs`, `voice_plans`, and `voice_presets`. |
| Frontend test baseline | PASS | `npm test -- --run` from `frontend` | Exit 0; `19 passed` in 9 files. |
| Frontend production build | PASS | `npm run build` from `frontend` | Exit 0; TypeScript and Vite build completed. |
| SQLite backup/restore fixture | PASS | New `test_backup_restores_database_and_artifacts_to_an_isolated_data_root` | Uses SQLite Backup API, validates `integrity_check`, restores the database and SHA-256-checked artifact into a new data root, and confirms the source artifact is unchanged. |
| Dependency reinstall / audit | NOT_RUN | `npm ci` / `npm audit` | Existing dependencies were used and no dependency upgrade or audit fix was run. Task preflight context reported six npm audit findings; that is not treated as a passing security check. |
| Cloud provider, paid TTS, model download | NOT_RUN | No cloud/model command invoked | Outside F01 and intentionally not inferred from fake or fixture tests. |

## Recovery boundary

`BackupService.create()` snapshots the database through SQLite Backup API and stores a sidecar artifact snapshot. The manifest carries the database SHA-256, byte size, integrity result, artifact snapshot name, and pointer count. `verify()` rechecks the database and every non-deleted artifact pointer against the snapshot.

`BackupService.restore_to_data_root()` requires the API and worker startup locks plus a new non-existent target data root that does not overlap the source root. It stages `studio.sqlite3` plus the artifact snapshot, validates the target database and pointers, then publishes the staged root. The fixture proves source data remains unchanged.

The existing HTTP restore endpoint still invokes the backward-compatible in-place `restore_to()` flow and does not yet accept a target data-root parameter. It must not be reported as the independent-root flow; target selection and confirmation belong to the later Storage UI work.
