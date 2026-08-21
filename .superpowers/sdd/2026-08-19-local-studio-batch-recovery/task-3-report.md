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
