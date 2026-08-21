# Task 4 Report: TranslationWorkflow, deterministic QA, editor revision and approval

## Status

DONE_WITH_CONCERNS

## Files changed

- `backend/app/modules/translation/qa.py`
- `backend/app/modules/translation/workflow.py`
- `backend/app/api/translation.py`
- `backend/app/main.py`
- `backend/tests/translation/test_workflow.py`
- `frontend/src/features/translation/TranslationEditor.tsx`
- `frontend/src/features/translation/TranslationEditor.test.tsx`

## RED evidence

Command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/translation/test_workflow.py -q
```

Output:

```text
ERROR backend\tests\translation\test_workflow.py
E   ModuleNotFoundError: No module named 'app.modules.translation.workflow'
1 error in 0.34s
```

Command:

```powershell
npm test -- --run src/features/translation/TranslationEditor.test.tsx
```

Output:

```text
FAIL src/features/translation/TranslationEditor.test.tsx
Error: Failed to resolve import "./TranslationEditor" from "src/features/translation/TranslationEditor.test.tsx".
Test Files  1 failed (1)
Tests  no tests
```

## GREEN evidence

Command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/translation/test_workflow.py -q
```

Output:

```text
......                                                                   [100%]
6 passed in 2.03s
```

Command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests/translation -q
```

Output:

```text
...................                                                      [100%]
19 passed in 5.71s
```

Command:

```powershell
npm test -- --run
```

Output:

```text
Test Files  2 passed (2)
Tests  5 passed (5)
```

Command:

```powershell
npm run build
```

Output:

```text
tsc -b && vite build
28 modules transformed.
dist/assets/index-Krqc5FGY.js  190.39 kB | gzip: 60.35 kB
built in 933ms
```

Command:

```powershell
.\.venv\Scripts\python -m ruff check backend/app/modules/translation/qa.py backend/app/modules/translation/workflow.py backend/app/api/translation.py backend/app/main.py backend/tests/translation/test_workflow.py
```

Output:

```text
All checks passed!
```

Command:

```powershell
.\.venv\Scripts\python -m ruff format --check backend/app/modules/translation/qa.py backend/app/modules/translation/workflow.py backend/app/api/translation.py backend/app/main.py backend/tests/translation/test_workflow.py
```

Output:

```text
5 files already formatted
```

Command:

```powershell
.\.venv\Scripts\python -m pytest backend/tests -q
```

Output:

```text
218 passed, 1 warning in 32.86s
```

## Implementation notes

- Added deterministic QA for empty/missing translation, length ratio, residual Han, protected numeric/unit tokens, locked glossary names, repetition, meta/markdown markers, and TTS text length.
- Added `TranslationWorkflow` orchestration for provider adapter calls, canonical SHA-256 cache keys, cache-hit segment rows, manual editor revisions, stale hash rejection, approval blocking, audit recording, and `TRANSLATION_APPROVED` transition.
- Added FastAPI translation routes for current run read, segment revision PATCH, and translation approval.
- Added `TranslationEditor` with source/target editing by source segment ID, issue severity filtering, saved revision hash handling, and conflict display without overwriting local draft text.

## Self-review

- Verified approval blocks any open `MAJOR` or `CRITICAL` issue; accepted-risk mutation is not exposed in this task slice.
- Verified cached translation rows use `was_cache_hit=True` and do not write usage ledger rows.
- Verified editor PATCH sends `expectedRunHash` and workflow/API reject stale hashes with `409`.
- Fixed self-review issues before final verification: provider fallback no longer depends on profile presence, and locked-term QA now uses the active glossary revision only.

## Concerns

- Full backend tests pass with the pre-existing SQLAlchemy metadata warning about unresolved FK cycles in `tests/db/test_schema.py`; no Task 4 failures remain.
- Workflow uses the injected/default adapter primitive directly. Real Qwen budget/cloud consent wiring remains governed by the existing adapter/guard primitives and was not exercised with paid provider calls.
