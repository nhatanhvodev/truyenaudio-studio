# Task 12 / C02 report

Status: DONE

## Delivered

- GlossaryEntry now carries description, forbidden forms, evidence, and a chapter scope (`scope_from_ordinal`/`scope_to_ordinal`) via additive migration `0008` (batch alter + `glossary_scope_order` check constraint). No new container entity was added (per translation-engine-research §5).
- `GlossaryCommand`/API request extended with the same optional fields; upsert creates a new immutable revision (superseding the active one) when any semantic field changes and is a no-op when nothing changed. Canonical glossary hash now covers the new fields.
- Validation before persistence: `GLOSSARY_SCOPE_INVALID` for reversed or <1 bounds; `GLOSSARY_CONFLICT_INTERNAL` when a locked target equals one of its own forbidden forms. Forbidden forms are normalized (trimmed, deduped, blanks dropped) and stored as JSON `list[str]`.
- Chapter-scoped application: `locked_rules_for_chapter(project, ordinal)` returns only locked entries whose scope covers the ordinal (unbounded entries still apply everywhere). TranslationWorkflow and RepairService now derive provider terms and QA rules per chapter (`chapter.ordinal`) through this helper, so a scoped term is never applied outside its scope.
- Deterministic QA gained a `forbidden_forms` rule: when the source contains a locked term and the target uses one of its forbidden renderings, a MAJOR `NAME`/`forbidden-form` issue is emitted (blocks approval together with locked-term/MAJOR rules). `run_deterministic_qa` stays backward compatible (`forbidden_forms` defaults empty).
- Downstream stale on glossary edit: `GlossaryService.upsert` now plans `Change.glossary(affected_chapter_ids)` through the existing `InvalidationGraph` (previously test-only), so editing a glossary term supersedes approved/review translation runs (and their audio/master/export chain) **only for chapters whose active source contains the term inside the entry scope**; other chapters stay approved. The invalidation result is surfaced as `GlossaryUpsertResult.invalidated`.

## Design notes / residual (visible follow-ups)

- Glossary scope is single-active per (project, source_term) for now: an upsert still supersedes the previous active entry of the same term. Storing multiple simultaneous chapter-interval variants of the same source term is not enabled in C02 (would require relaxing the one-active invariant + overlap-conflict rules); not required by any current acceptance. Per-chapter effective-glossary snapshotting/cache keys remain C05 scope.
- The migration changes the canonical project glossary hash for existing rows (new fields now part of the hash), which only affects future run/cache keys; no stored run is mutated by the migration.
- No fixture/API consumer outside the glossary domain was broken: read/upsert routes return the same shapes plus the new optional keys.

## Validation

- `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests/translation/test_glossary.py backend/tests/translation/test_glossary_c02_scope.py backend/tests/translation/test_selective_repair.py backend/tests/translation/test_workflow.py backend/tests/db/test_schema.py backend/tests/projects/test_invalidation.py -q`
  - 61 passed (includes 9 new C02 tests).
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q`
  - 573 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed (models, glossary, qa, workflow, repair, api, tests, migration).
- Migration 0008 upgrade/downgrade exercised by the schema round-trip tests.

## Notes

- Contract/fixture validation only; no provider/cloud behavior changed in this task.
