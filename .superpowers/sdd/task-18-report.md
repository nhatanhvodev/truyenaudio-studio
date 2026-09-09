# Task 18 / J01 report — rounds 1–2 (PARTIAL)

Status: PARTIAL — enqueue-plan seam + batch child plans done; real stage handlers + worker wiring remain.

## Delivered (round 1)

- `jobs.plan_json` column (additive migration `0013`) + ORM `Job.plan_json`.
- `JobRunner.enqueue(..., plan=...)` stores an immutable plan payload on the job; idempotent re-enqueue with the same key **never overwrites** the first plan (tested). `JobView.plan` exposes the parsed payload.
- `modules/jobs/handlers.py`: `PLAN_REQUIRED_KINDS` (TRANSLATE/REVIEW/REPAIR_TRANSLATION/PREVIEW_TTS/SYNTHESIZE/MASTER/AUDIO_QA/EXPORT) and `require_plan(kind, plan)` fail closed when the immutable plan is missing or lacks `projectId/profileId/cloudConsentId/budgetAuthorizationId` (`<KIND>_PLAN_REQUIRED`). Import-type local jobs need no plan.
- Tests: runner plan round-trip + immutability (2), handler plan matrix (3).

## Delivered (round 2)

- `BatchCoordinator.enqueue_batch` now attaches an immutable `_child_plan` to every cloud-capable child job (TRANSLATE/REVIEW/SYNTHESIZE): `stage/projectId/quoteId/revisionId/profileId/cloudConsentId/budgetAuthorizationId/category` from the validated `BatchCloudAuthorization` (local stages carry no plan). Children are therefore self-describing for a future worker handler and satisfy `require_plan`.
- Tests: cloud child plan round-trip + `require_plan` acceptance, non-cloud child plan absent.

## Remaining for J01 acceptance (next round)

- Wire production handlers (translate/review/polish/repair/summarize via existing workflow/review services) into `Worker.build_default_handlers`, executing each job's plan under RecoveryJobContext: re-validate profile revision/consent/budget at claim, dispatch adapter with network outside the write transaction, segment-level idempotency + checkpoint via recovery artifacts, and mark partially-sent attempts `billingUnknown`. Crash-before/after-send/commit coverage will reuse the existing `tests/integration/worker_fixture` process harness with the real handlers.

## Validation

- Round 1: `backend/tests/jobs/test_runner.py backend/tests/jobs/test_job_plan.py backend/tests/db/test_schema.py -q` — 70 passed.
- Round 2: `backend/tests/jobs/test_batch.py -q` — 8 passed; full `backend/tests/jobs` — 71 passed.
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 614 passed (round 1: 612), 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed.
- No provider/cloud call added.
