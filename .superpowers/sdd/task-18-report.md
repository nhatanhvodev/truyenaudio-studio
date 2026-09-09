# Task 18 / J01 report — rounds 1–3 (PARTIAL)

Status: PARTIAL — plan seam + batch child plans + real TRANSLATE handler done; other stage handlers + harness integration remain.

## Delivered (round 1)

- `jobs.plan_json` column (additive migration `0013`) + ORM `Job.plan_json`.
- `JobRunner.enqueue(..., plan=...)` stores an immutable plan payload on the job; idempotent re-enqueue with the same key **never overwrites** the first plan (tested). `JobView.plan` exposes the parsed payload.
- `modules/jobs/handlers.py`: `PLAN_REQUIRED_KINDS` + `require_plan` fail closed (missing `projectId/profileId/cloudConsentId/budgetAuthorizationId` -> `<KIND>_PLAN_REQUIRED`); local kinds need no plan.
- Tests: runner plan round-trip + immutability (2), handler plan matrix (3).

## Delivered (round 2)

- `BatchCoordinator.enqueue_batch` attaches an immutable `_child_plan` (stage/projectId/quoteId/revisionId/profileId/cloudConsentId/budgetAuthorizationId/category) to every cloud-capable child job (TRANSLATE/REVIEW/SYNTHESIZE) from the validated `BatchCloudAuthorization`; local stages carry no plan.
- Tests: cloud child plan round-trip + `require_plan` acceptance, non-cloud child plan absent.

## Delivered (round 3)

- `modules/jobs/execution_handlers.py` + `build_translate_handler(settings, db_path=None)`: executes the real TRANSLATE path under the worker:
  - `require_plan` at execution (missing plan fails the job `TRANSLATE_PLAN_REQUIRED`, not retryable);
  - chapter revision must equal `plan.revisionId` (`TRANSLATE_REVISION_STALE`) and the plan profile must exist and be enabled (`PROFILE_NOT_FOUND`/`PROFILE_DISABLED`);
  - fake adapter => local deterministic hanviet workflow; qwen/gemini => the existing guarded API workflow scopes (`_qwen_workflow`/`_gemini_workflow`) with plan consent/budget ids; unsupported provider => `TRANSLATE_PROVIDER_UNSUPPORTED:<adapter>`.
  - Rejections are `HandlerUnavailable` subclasses so `Worker.run_once` fails the job with the precise code; no secret/log/cloud call added.
- `Worker.build_default_handlers(settings=None)` keeps the old registry when settings is None (tests unchanged) and registers the real TRANSLATE handler when settings are provided; `build_default_worker` passes settings.
- Tests (3, through `Worker.run_once` on the migrated engine): fake provider job completes -> SUCCEEDED + run row created; missing plan -> FAILED `TRANSLATE_PLAN_REQUIRED`; stale revision -> FAILED `TRANSLATE_REVISION_STALE`.

## Remaining for J01 acceptance (next round)

- Wire REVIEW/REPAIR_TRANSLATION/summarize (and audio stages when reached) into `build_default_handlers` the same way; crash-before/after-send/commit process-harness coverage with the real handlers (`tests/integration/worker_fixture`); per-segment checkpoints on long jobs.

## Validation

- Round 1: `backend/tests/jobs/test_runner.py backend/tests/jobs/test_job_plan.py backend/tests/db/test_schema.py -q` — 70 passed.
- Round 2: `backend/tests/jobs/test_batch.py -q` — 8 passed; full `backend/tests/jobs` — 71 passed.
- Round 3: `backend/tests/jobs/test_execution_handlers.py -q` — 3 passed; `backend/tests/jobs backend/tests/translation` — 153 passed.
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 617 passed (614 after round 2; +3 handler tests), 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed.
- No provider/cloud call added (fake provider only in tests).
