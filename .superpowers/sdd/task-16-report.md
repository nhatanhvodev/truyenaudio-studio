# Task 16 / C05 report

Status: DONE

## Delivered

- StoryMemoryEntry approval model (additive migration `0012`): `status` (server-default `APPROVED` so legacy rows stay usable), `source_run_id -> translation_runs`, `evidence_segment_ids_json`.
- `StoryMemoryService` lifecycle:
  - `memory_for`/`summaries_for`/`hash_for` expose only APPROVED rows **whose evidence run is still APPROVED** (source_run_id NULL rows are legacy/user facts) — candidates, rejected rows and memory backed by a superseded run are excluded at read time (no sweep needed).
  - `candidates_for`, `create_candidate` (user/owner-authored or future summarize-stage proposals), `approve(project, id, source_run_id, evidence_segment_ids)` — evidence must be an APPROVED translation run of the same project and segments under that run's source revision (`MEMORY_EVIDENCE_RUN_NOT_APPROVED/_SEGMENTS_INVALID/...`), `reject`.
  - Canonical revision-hash schema is unchanged, so stored TranslationRun hashes stay comparable.
- TranslationWorkflow `_story_memory` now delegates to the status-aware service (single source of truth) — candidates can never reach a prompt.
- New deterministic `context_engine`: `ContextItem` + `select_context(items, token_budget)` — greedy by (priority, id), `excluded` trace with reason `BUDGET`, deterministic `sha256` over the snapshot; identical snapshots => identical selection/hash. Source text is never an input to the engine and is never truncated by it (source handling stays in the caller).
- `/api/projects/{id}/memory` router (GET context approved+hash, GET/POST candidates, POST approve, POST reject) registered in the app.

## Design notes / residual (visible follow-ups)

- Candidate *generation* for a chapter (the summarize stage) is a guarded job-stage concern: no cloud call is hidden here — candidates are created via API/service and a guarded summarize stage (J01/J02 territory) will later enqueue them. Locked-glossary priority is enforced at the glossary/terms layer (C02); the engine's `priority` field lets facts be ranked ahead of summaries when J03/C06 feed it.
- Overflow behavior: the engine degrades by excluding lower-priority context and never truncates source; the token budget is a context budget applied by callers before prompt assembly (C05 acceptance "không truncate source" holds by construction).

## Validation

- `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests/translation/test_story_memory.py backend/tests/translation/test_story_memory_c05_approval.py backend/tests/translation/test_context_engine.py backend/tests/translation/test_selective_repair.py backend/tests/translation/test_workflow.py backend/tests/db/test_schema.py -q` — 49 passed (includes 6 new C05 tests: candidate isolation, approve evidence matrix, rejected exclusion, cross-project run, stale-on-supersede, engine budget/priority/hash).
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 603 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed; migration 0012 upgrade/downgrade exercised by schema round-trip tests.
