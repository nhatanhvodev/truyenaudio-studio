# Task 14 / C03 report

Status: DONE

## Delivered

- New `translation_memory` table (additive migration `0010`): id, project_id, source_hash, source_text, target_text, source_language, target_language, style_revision_id (nullable), glossary_hash, approved_run_id, timestamps; lowercase-sha256 checks on hash columns and a (project_id, source_hash) index.
- `translation_memory` service:
  - `record_approved_run(session, run_id)` records one READY pair per segment of an APPROVED run only (`TRANSLATION_RUN_NOT_APPROVED` otherwise). One row per fingerprint (project, source_hash, language pair, glossary hash, style revision); a newer approved run replaces the older row for the same fingerprint, so latest approved rendering wins.
  - `exact_match(...)` reuses a pair only when the full fingerprint matches AND the approving run is still `APPROVED` (a superseded run becomes ineligible at read time — no background sweep). Cross-project, wrong-glossary and superseded cases return None.
  - `count_for_project(...)` helper.
- Workflow wiring:
  - `approve_revision` records approved pairs into TM before commit (only approved output becomes memory — never drafts/review output).
  - `enqueue_translation` performs an exact-match lookup per segment (before provider dispatch) and passes the matched pair as `tm_list` on the `TranslationRequest` (previously always `()`), so native-MT providers (Qwen `translation_options.tm_list`) receive it. Chat adapters ignore `tm_list` until the C05 context engine folds approved examples into the prompt.
- Fuzzy matching is intentionally NOT implemented here: it is a suggestion surfaced by UI/context (plan rule: fuzzy không tự approve / không ghi đè).

## Design notes / residual (visible follow-ups)

- `style_revision_id` is stored but currently always NULL on rows because TranslationRun does not yet carry a style revision (C01 profile data exists; run-level style wiring lands with the C05 context/execution snapshot). Exact-match fingerprint still fully uses glossary hash, so glossary-scoped reuse is honored from day one.
- Exact-match pairs do not enter the translation cache key: availability of a pair for a given (source text, fingerprint) is stable once an approved run exists, so cache reuse cannot silently drop TM (documented reasoning in report).

## Validation

- `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests/translation/test_translation_memory.py backend/tests/translation/test_workflow.py backend/tests/translation/test_selective_repair.py backend/tests/db/test_schema.py -q` — focused suites green (TM tests 5, plus workflow/repair/schema regression).
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 585 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed; migration 0010 upgrade/downgrade exercised by the schema round-trip test (raw `DROP TABLE` used in downgrade because Alembic's SQLite `op.drop_table` reflection misfires on this file).
