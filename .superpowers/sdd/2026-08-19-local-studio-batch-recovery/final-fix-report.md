# Final Fix Report: Plan 3 Final Review Wave

Status: complete.

Base before this wave: `708bc83`.

## Scope Fixed

- Critical artifact-root mismatch: backup/restore and cleanup now resolve artifact DB rows against both the normal `data_root` store and producer-created `data_root/artifacts` store, while preserving path containment and checksum checks.
- Import preview wiring: added `POST /api/projects/{project_id}/chapters/import/preview` for `EPUB`, `DOCX`, and `LOCAL_FOLDER`; the route returns candidates and does not write chapters/source revisions. The active import screen now previews EPUB/DOCX/folder candidates and confirms only after mapping review.
- Migration startup: added `scripts/migrate.ps1` and made `run-studio.bat` run Alembic `upgrade head` after preflight and before starting uvicorn/worker.
- Durable SSE progress: `/api/jobs/snapshot` and `/api/jobs/events` now use the durable `event_log` stream, and the UI listens to `/api/events`.
- Sent-but-unclear billing: `QwenMtAdapter` and `GptLunaReviewer` now map built-in timeout plus `httpx.TimeoutException`/`httpx.TransportError` after dispatch uncertainty to `ProviderBillingUnknown`, with no network calls in tests.

## RED Evidence

- Focused backend RED initially failed for the intended gaps after setup correction:
  - Storage: missing `audio/chapter-1.mp3` under `data_root/artifacts`.
  - Cleanup: no candidate for `audio/preview.wav` under `data_root/artifacts`.
  - Import preview: missing preview route behind the existing CSRF middleware.
  - Launcher: no `scripts\migrate.ps1` invocation and no migration script.
  - Jobs SSE: `/api/jobs/events` replayed timestamp-based events instead of numeric durable sequence.
  - Providers: `httpx.ReadTimeout`/`httpx.ConnectError` propagated instead of `ProviderBillingUnknown`.
- Frontend RED:
  - `JobProgress` still opened `/api/jobs/events`.
  - Import screen had no local-folder preview controls.

## GREEN Evidence

- Focused final-review regressions:
  - `.\.venv\Scripts\python -m pytest backend/tests/storage/test_backup_restore.py::test_restore_verifies_rows_under_data_root_artifacts_when_paths_are_artifact_relative backend/tests/storage/test_cleanup.py::test_cleanup_resolves_artifact_relative_rows_under_data_root_artifacts backend/tests/api/test_import_preview_api.py backend/tests/scripts/test_launcher_migrations.py backend/tests/api/test_jobs_api.py::test_jobs_events_replays_after_last_event_id_header backend/tests/providers/test_qwen_mt_contract.py::test_qwen_httpx_timeout_or_transport_after_dispatch_marks_billing_unknown backend/tests/providers/test_gpt_luna_reviewer_contract.py::test_luna_httpx_timeout_or_transport_after_dispatch_marks_billing_unknown -q`
  - Result: `11 passed in 2.33s`.
- Impacted backend suites:
  - `.\.venv\Scripts\python -m pytest backend/tests/storage backend/tests/api/test_import_preview_api.py backend/tests/api/test_jobs_api.py backend/tests/api/test_sse_resume.py backend/tests/providers/test_qwen_mt_contract.py backend/tests/providers/test_gpt_luna_reviewer_contract.py backend/tests/scripts/test_launcher_migrations.py -q`
  - Result: `38 passed in 7.12s`.
- Ruff touched backend files:
  - `.\.venv\Scripts\python -m ruff check ...`
  - Result: `All checks passed!`.
- Full backend suite after final code changes:
  - `.\.venv\Scripts\python -m pytest backend/tests -q`
  - Result: `356 passed, 1 warning in 70.60s`.
  - Warning: existing SQLAlchemy metadata FK-cycle warning in `tests/db/test_schema.py::test_frozen_migration_matches_orm_metadata_server_defaults`.
- Frontend:
  - `npm test -- --run`
  - Result: `7 passed`, `16 tests passed`.
  - `npm run build`
  - Result: production build passed.
- Playwright:
  - `npm exec playwright test e2e/batch-recovery.spec.ts`
  - Result: `1 passed`.
- Migration script verification:
  - `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\migrate.ps1` with a temporary `STUDIO_DATA_ROOT`.
  - Result: migrations `0001` through `0004` applied and script printed `Database migrations applied`.

## Concerns

- The full backend suite still emits the pre-existing SQLAlchemy FK-cycle warning from schema comparison.
- The Playwright harness remains the existing fake-audio local E2E path; no paid provider/network smoke was run.
