# Task 17 / C06 report

Status: DONE

## Delivered

- Approve-time segment coverage guard: `TranslationWorkflow.approve_revision` now calls `_require_complete_segment_coverage(run)` before QA blockers. A run may only be approved when its `TranslationSegment` set exactly matches the source revision's `SourceSegment` set; a missing or extra segment raises `ApprovalBlocked("TRANSLATION_RUN_SEGMENTS_INCOMPLETE:missing=N,extra=M")` and can never be approved (missing/extra segments are not force-approvable). The same guard applies to runs produced by `revise_segment`.
- Existing C06 mechanisms verified in place and covered by regression tests in this round: expected-hash on edit/approve (`RevisionConflict` on mismatch), hard QA blockers (MAJOR/CRITICAL) blocking approve with explicit `force` override policy, deterministic QA rules (completeness/length/residual Han/number-unit/locked-term/forbidden-form/repetition/meta-markdown/TTS length), repair proposal immutability + conflict handling, and upstream invalidation (glossary edits via C02, story-memory staleness at read in C05, TM freshness in C03).

## Design notes / residual (visible follow-ups)

- Repair dispatch still runs the translator synchronously in the request path today; moving repair/QA model dispatch behind a guarded job stage is the J01 (worker/handler) scope, not duplicated here.
- Invalidation for source re-imports (Change.source_revision) is exercised by InvalidationGraph tests; production wiring on the import endpoint belongs to the U03/import review work so it stays co-located with that API change.

## Validation

- `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests/translation/test_workflow_c06_coverage.py -q` — 4 passed (missing segment, extra segment, full-coverage approve, revise-then-approve still guarded).
- Regression: workflow/repair/glossary/story-memory/context-engine suites — 39 passed.
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 607 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed.
