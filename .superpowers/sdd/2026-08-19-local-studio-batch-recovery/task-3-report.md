# Task 3 Report: Story memory, risk reviewer, and selective repair

## Status

Implemented and committed-ready.

## Scope

- Added `StoryMemoryService.memory_for(project_id, ordinal)` with the required ordinal window:
  `valid_from_ordinal <= ordinal` and `valid_to_ordinal IS NULL OR valid_to_ordinal >= ordinal`.
- Added `ReviewService.enqueue(...)` that queues only open major/critical QA issue segments plus explicit user-selected segment IDs.
- Added `RepairService.propose(...)` and `accept_repair(...)`.
  - Proposal generation does not mutate the current translation run.
  - Accepting a proposal creates a new review run, inherits unchanged rows, supersedes the base run, and reruns deterministic QA.
  - Repair cost quoting uses category `QA_REPAIR`.
- Added `GptLunaReviewer` with model `gpt-5.6-luna`, low deterministic temperature, closed JSON schema, exact finding validation, guard-before-HTTP behavior, usage mapping to `INPUT_TOKEN` and `OUTPUT_TOKEN`, and `ProviderBillingUnknown` on timeout after dispatch.
- Added `/api/chapters/{chapter_id}/review/enqueue` and registered it in `main.py`.
- Added `RepairDiff` and focused UI tests for word-level diff display and explicit accept/reject behavior.

## RED Evidence

Command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/translation/test_story_memory.py backend/tests/translation/test_selective_repair.py backend/tests/providers/test_gpt_luna_reviewer_contract.py -q
```

Result before implementation:

```text
ModuleNotFoundError: No module named 'app.modules.translation.story_memory'
ModuleNotFoundError: No module named 'app.modules.translation.repair'
ModuleNotFoundError: No module named 'app.providers.gpt_luna_reviewer'
3 errors during collection
```

## GREEN Evidence

Focused Task 3 backend:

```text
7 passed in 1.42s
```

Broader translation/provider verification from the brief:

```text
30 passed in 6.27s
```

Relevant existing provider/compliance/budget/batch checks:

```text
35 passed in 8.25s
```

Frontend RepairDiff:

```text
2 passed
```

Frontend build:

```text
tsc -b && vite build passed
```

Ruff on touched backend files:

```text
All checks passed!
```

## Ruling

Ruling: The Task 3 brief listed no database model or migration files for repair proposal persistence, while `accept_repair(proposal_id, expected_hash)` needs the proposed replacements after a later user action. I used a minimal in-process proposal store in `repair.py` to keep the task inside the allowed file scope. This supports same-process propose/accept and preserves the no-auto-accept rule; durable proposal storage should be added in a later explicitly schema-scoped task if restart-safe proposals are required.

## Not Implemented

- No Official Yuewen API, scraper, URL fetch, auto-upload, multi-voice, cloud TTS, backup/cleanup, diagnostics, Sol/Gemini adjudication, broad retry, or recovery behavior.
- No real provider smoke test was run; tests use fakes/recorded fixtures only.

## Fix Round 1

Review findings addressed:

- `accept_repair` now rejects stale proposals when the base run is no longer the current `REVIEW` run or its content hash no longer matches the proposal base hash.
- Repair requests now pass `billing_category="QA_REPAIR"` through `OperationContext`; the shared Qwen adapter defaults existing translation flow to `REGULAR` and uses the context category for repair guard evaluation.
- Repair proposals now store the current story-memory hash used for generation; accepted repair runs record that hash, and repair cache keys include it.
- Luna reviewer now converts sent-but-unclear post-dispatch failures to `ProviderBillingUnknown`, including HTTP status failures, malformed JSON/content, missing choices, invalid schema, and usage validation failures.

RED command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/translation/test_selective_repair.py backend/tests/providers/test_gpt_luna_reviewer_contract.py -q
```

RED output:

```text
7 failed, 6 passed in 2.67s
```

GREEN commands and outputs:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/translation/test_selective_repair.py backend/tests/providers/test_gpt_luna_reviewer_contract.py -q
```

```text
13 passed in 2.53s
```

```powershell
.\.venv\Scripts\python -m pytest backend/tests/translation backend/tests/providers/test_gpt_luna_reviewer_contract.py -q
```

```text
37 passed in 11.75s
```

```powershell
.\.venv\Scripts\python -m pytest backend/tests/providers/test_qwen_mt_contract.py backend/tests/providers/test_fake_contracts.py backend/tests/compliance/test_cloud_translation_guard.py backend/tests/budgets/test_guard.py backend/tests/jobs/test_batch.py -q
```

```text
35 passed in 13.34s
```

```powershell
.\.venv\Scripts\python -m ruff check backend/app/contracts.py backend/app/modules/translation/repair.py backend/app/providers/qwen_mt.py backend/app/providers/gpt_luna_reviewer.py backend/tests/translation/test_selective_repair.py backend/tests/providers/test_gpt_luna_reviewer_contract.py
```

```text
All checks passed!
```

```powershell
npm test -- --run src/features/translation/RepairDiff.test.tsx
```

```text
2 passed
```
