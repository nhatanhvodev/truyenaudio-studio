# Task 2 Report: Batch coordinator, 50-chapter queue, cursor pagination

## Status

Implemented and committed-ready.

## RED Evidence

Command:

```powershell
cd D:\truyenaudio-studio
.\.venv\Scripts\python -m pytest backend/tests/jobs/test_batch.py backend/tests/api/test_pagination.py -q
```

Observed expected RED:

```text
ModuleNotFoundError: No module named 'app.modules.jobs.batch'
```

## Implementation Summary

- Added `backend/app/modules/jobs/batch.py`.
  - `BatchCoordinator.enqueue_batch(project_id, chapter_ids, stage, quote_id)`.
  - Enforces 1..50 chapter IDs and uniqueness.
  - Validates all chapters belong to the requested project and have active source revisions.
  - Reuses existing `JobRunner.enqueue` idempotency instead of adding a mega-job or replacing the queue.
  - Creates one child job per chapter.
  - Child idempotency key is SHA-256 over batch input, chapter ID, stage, and active source revision.
  - `BatchView` progress is derived from persisted job rows.

- Added `backend/app/modules/projects/queries.py`.
  - `Page[ChapterSummary]` with default `limit=25`, max `100`.
  - Stable order by `(ordinal, id)`.
  - Uses `limit + 1` for next-cursor detection.
  - Uses separate `COUNT(*)` for total.
  - Cursor is base64url JSON signed by HMAC.
  - Summaries expose ID/title/state/progress/cost/issues/hashes and do not select or return full source/translation text.

- Added `backend/app/api/batches.py`.
  - `POST /api/batches` queues selected chapters for a stage.

- Updated `backend/app/api/projects.py`.
  - `GET /api/projects/{project_id}/chapters` returns paginated summaries.

- Updated `backend/app/main.py`.
  - Registers the batch router.
  - Passes the startup CSRF token as the chapter cursor signing secret.

- Added `frontend/src/features/batch/BatchQueue.tsx`.
  - Paged chapter summary loading.
  - Selection capped at 50.
  - Queues `TRANSLATE` jobs through `/api/batches`.
  - Does not preload editor text.

- Updated `frontend/src/routes/router.tsx`.
  - Adds `/projects/:projectId/batch`.

## Scope Ruling

The brief says paid/cloud stage enqueue must use existing guard/budget primitives. In the current Task 2 interface, `stage: JobKind` alone does not identify local vs cloud provider, and there is no existing batch/job field that can safely bind one batch quote to all child jobs without schema changes. I therefore kept Task 2 queueing provider-neutral and did not create any paid provider calls. Existing cloud/provider authorization paths remain enforced at execution endpoints such as Qwen translation. This avoids inventing a new budget contract before the later confirmation/execution tasks.

## Verification

Focused RED/GREEN:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/jobs/test_batch.py backend/tests/api/test_pagination.py -q
```

Result:

```text
6 passed in 2.95s
```

Ruff on touched backend files:

```powershell
.\.venv\Scripts\python -m ruff check backend\app\modules\jobs\batch.py backend\app\modules\projects\queries.py backend\app\api\batches.py backend\app\api\projects.py backend\app\main.py backend\tests\jobs\test_batch.py backend\tests\api\test_pagination.py
```

Result:

```text
All checks passed!
```

Relevant backend regression:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/api backend/tests/jobs/test_runner.py -q
```

Result:

```text
64 passed in 14.10s
```

Full backend verification:

```powershell
.\.venv\Scripts\python -m pytest backend/tests -q
```

Result:

```text
284 passed, 1 warning in 53.95s
```

Warning: existing SQLAlchemy metadata cycle warning from `tests/db/test_schema.py`.

Frontend build:

```powershell
npm run build
```

Result:

```text
tsc -b && vite build
✓ built in 1.03s
```

Frontend tests:

```powershell
npm test -- --run
```

Result:

```text
5 passed, 12 tests passed
```

## Concerns

- None for Task 2 after fix round 1.

## Fix Round 1

Review findings addressed:

- Paid/cloud-capable stages now require `BatchCloudAuthorization` before any child jobs are persisted.
- The batch endpoint accepts existing `providerProfileId`, `cloudConsentId`, `budgetAuthorizationId`, `quoteId`, and explicit `estimatedUnits` values.
- Guard validation uses existing `CloudCallGuard` and `BudgetGuard` primitives with no paid provider execution.
- Missing or denied guard inputs return `422` with a clear block reason and leave the jobs table untouched.
- `pause_requested` is checked between child job enqueue attempts; already-created jobs are left queued/running and subsequent children are not created.
- `BatchQueue.tsx` no longer submits a provider-neutral cloud-capable batch; it requires the guard input fields before posting.

Fix RED command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/jobs/test_batch.py backend/tests/api/test_pagination.py -q
```

Fix RED result:

```text
ImportError: cannot import name 'BatchCloudAuthorization' from 'app.modules.jobs.batch'
```

Focused Task 2 tests:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/jobs/test_batch.py backend/tests/api/test_pagination.py -q
```

Result:

```text
10 passed in 5.75s
```

Ruff on touched backend files:

```powershell
.\.venv\Scripts\python -m ruff check backend\app\modules\jobs\batch.py backend\app\api\batches.py backend\tests\jobs\test_batch.py backend\tests\api\test_pagination.py
```

Result:

```text
All checks passed!
```

Relevant backend regressions:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/api backend/tests/jobs/test_runner.py backend/tests/budgets/test_guard.py backend/tests/compliance/test_cloud_translation_guard.py -q
```

Result:

```text
84 passed in 19.09s
```

Frontend build:

```powershell
npm run build
```

Result:

```text
tsc -b && vite build
✓ built in 1.33s
```

Frontend tests:

```powershell
npm test -- --run
```

Result:

```text
5 passed, 12 tests passed
```
