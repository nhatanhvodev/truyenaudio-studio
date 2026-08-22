# Task 7 Report: Structured diagnostics, SSE resume, batch/recovery E2E

## Status

Implemented and verified.

## RED evidence

Initial focused command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/diagnostics backend/tests/api/test_sse_resume.py -q
```

Expected RED result:

```text
ModuleNotFoundError: No module named 'app.modules.diagnostics'
```

After implementation the same focused command passed:

```text
2 passed in 0.89s
```

## Implementation summary

- Added `app.modules.diagnostics` with allowlisted JSONL diagnostics, UTC daily rotation naming, secret/token scrubbing, 500-character error summaries, and blocked context fields for source/translation/request bodies.
- Added `/api/events` with monotonic numeric `sequenceId` and `Last-Event-ID`/`after` resume.
- Added `/api/diagnostics/health` and `/api/diagnostics/export`.
- Diagnostic ZIP contains nonsecret config, version, health, and redacted logs by default. User sample text is included only when `includeSample` is explicitly true.
- Added old diagnostic JSONL retention through storage cleanup preview candidates (`diagnostic_log`) instead of broad deletion.
- Added frontend Diagnostics route/page and blob export helper.
- Added Playwright `e2e/batch-recovery.spec.ts` using loopback-only fixtures: 50 pasted chapters, paged batch list, fake local translation/TTS, private/public exports, checksum-visible export success, and diagnostic ZIP scan.

## Verification

```text
.\.venv\Scripts\python -m pytest backend/tests/diagnostics backend/tests/api/test_sse_resume.py -q
2 passed in 0.89s
```

```text
.\.venv\Scripts\python -m pytest backend/tests/diagnostics backend/tests/api/test_sse_resume.py backend/tests/api/test_health.py backend/tests/storage/test_cleanup.py -q
13 passed in 2.12s
```

```text
.\.venv\Scripts\python -m ruff check backend/app/modules/diagnostics backend/app/api/events.py backend/app/api/diagnostics.py backend/app/modules/storage/cleanup.py backend/app/main.py backend/tests/diagnostics backend/tests/api/test_sse_resume.py
All checks passed
```

```text
cd frontend
npm test -- --run
7 passed, 15 tests passed
```

```text
cd frontend
npm run build
✓ built
```

```text
cd frontend
npm exec playwright test e2e/batch-recovery.spec.ts
1 passed
```

```text
.\.venv\Scripts\python -m pytest backend/tests -q
341 passed, 1 warning in 55.58s
```

The backend full-suite warning is the existing SQLAlchemy metadata cycle warning in `tests/db/test_schema.py`.

## Concerns

- Browser E2E cannot directly kill/restart the worker because the current Playwright harness runs a single uvicorn web server and the fake UI pipeline is synchronous. Restart/no-duplicate worker behavior remains covered by deterministic backend integration tests in `backend/tests/integration/test_worker_recovery.py`, which were included in the full backend suite.
- No paid network, Official Yuewen API, scraper/URL fetch, auto-upload, multi-voice, or cloud TTS path was added.

## Fix round 1

### RED evidence

```text
.\.venv\Scripts\python -m pytest backend/tests/diagnostics backend/tests/api/test_sse_resume.py -q
3 failed, 2 passed in 2.01s
```

Expected failures:

- JSONL still contained `errorSummary`, violating the exact interface allowlist.
- `/api/events` resumed with a duplicate reordered job after `updated_at` changed.
- `/api/events` emitted only job events and omitted audit/usage rows.

### Changes

- Added migration `0004_event_log` and ORM model `EventLog` as the durable monotonic sequence source for `/api/events`.
- `/api/events` now backfills event sequence rows for job, audit, and usage rows, then serves payloads ordered by durable `event_log.sequence_id`.
- Audit SSE payloads omit `redacted_details`; usage payloads expose billing metadata only; all emitted string fields pass through diagnostics scrubbing.
- Removed `errorSummary` from default JSONL output to keep the field allowlist exact. The 500-character cap remains covered by `summarize_error()` for sanctioned diagnostics summaries outside the JSONL field list.
- Added fake-mode `/api/diagnostics/fake-recovery/check` for browser-level recovery invariants in Playwright: duplicate READY cache keys, duplicate READY export manifests, and missing READY artifact count.

Ruling: the current Playwright harness starts only the API server and exercises synchronous fake UI operations, so it cannot kill/restart a real background worker from the browser without changing the harness architecture. The fix adds a browser-visible loopback check, available only with `STUDIO_FAKE_AUDIO=1`, and the full backend suite still covers actual worker kill/restart recovery through deterministic integration tests.

### Verification

```text
.\.venv\Scripts\python -m pytest backend/tests/diagnostics backend/tests/api/test_sse_resume.py -q
5 passed in 1.65s
```

```text
.\.venv\Scripts\python -m pytest backend/tests/db/test_schema.py backend/tests/api/test_sse_resume.py backend/tests/diagnostics -q
23 passed, 1 warning in 6.36s
```

```text
.\.venv\Scripts\python -m ruff check backend/app/api/events.py backend/app/api/diagnostics.py backend/app/modules/diagnostics backend/app/db/models.py backend/migrations/versions/0004_event_log.py backend/tests/api/test_sse_resume.py backend/tests/diagnostics/test_redaction.py backend/tests/db/test_schema.py
All checks passed
```

```text
.\.venv\Scripts\python -m pytest backend/tests -q
344 passed, 1 warning in 67.37s
```

```text
cd frontend
npm test -- --run
7 passed, 15 tests passed
```

```text
cd frontend
npm run build
✓ built
```

```text
cd frontend
npm exec playwright test e2e/batch-recovery.spec.ts
1 passed
```

## Fix round 2

### RED evidence

```text
.\.venv\Scripts\python -m pytest backend/tests/api/test_sse_resume.py backend/tests/api/test_diagnostics_recovery.py backend/tests/diagnostics -q
2 failed, 5 passed in 2.91s
```

Expected failures:

- Event-log backfill raised `UNIQUE constraint failed: event_log.entity_type, event_log.entity_id` when another stream inserted the same event row between read and commit.
- `POST /api/diagnostics/fake-recovery/run` returned 405 because the E2E-visible worker recovery endpoint did not exist.

### Changes

- Made event-log backfill tolerate concurrent first-stream races by rolling back duplicate insert `IntegrityError` instead of failing the SSE response.
- Added fake/test-mode `POST /api/diagnostics/fake-recovery/run`, guarded by `STUDIO_FAKE_AUDIO=1`.
- The endpoint creates an expired `RUNNING` job with an open attempt, runs real `Worker.run_once()` once to execute `recover_expired()`, advances a fake clock past retry delay, runs `Worker.run_once()` again to claim and complete the recovered job, then returns worker-run count, worker-driven recovery count, recovered job status, and before/after duplicate/missing artifact invariants.
- Updated Playwright to call `/api/diagnostics/fake-recovery/run` and assert `workerDrivenRecoveryCount == 1`, `recoveredJobStatus == "SUCCEEDED"`, and no duplicate READY cache keys/export manifests after recovery.

Ruling: literal OS process kill remains outside the current Playwright harness because it owns a single uvicorn web server and no separate worker process. The browser-visible endpoint now exercises the same backend `Worker.run_once()` and `JobRunner.recover_expired()` path over an expired RUNNING lease, so the E2E observes worker-driven recovery rather than only static invariant checking.

### Verification

```text
.\.venv\Scripts\python -m pytest backend/tests/api/test_sse_resume.py backend/tests/api/test_diagnostics_recovery.py backend/tests/diagnostics -q
7 passed in 2.50s
```

```text
.\.venv\Scripts\python -m pytest backend/tests/api/test_sse_resume.py backend/tests/api/test_diagnostics_recovery.py backend/tests/diagnostics backend/tests/db/test_schema.py -q
25 passed, 1 warning in 8.20s
```

```text
.\.venv\Scripts\python -m ruff check backend/app/api/events.py backend/app/api/diagnostics.py backend/tests/api/test_sse_resume.py backend/tests/api/test_diagnostics_recovery.py backend/tests/diagnostics
All checks passed
```

```text
cd frontend
npm test -- --run
7 passed, 15 tests passed
```

```text
cd frontend
npm run build
✓ built
```

```text
.\.venv\Scripts\python -m pytest backend/tests -q
346 passed, 1 warning in 64.76s
```

```text
cd frontend
npm exec playwright test e2e/batch-recovery.spec.ts
1 passed
```
