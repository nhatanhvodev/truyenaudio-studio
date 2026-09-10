# Task 29 / U04 report — round 1 (PARTIAL)

Status: PARTIAL — draft persistence with optimistic concurrency shipped (service + API); editor wiring and draft-driven streaming remain.

## Delivered (round 1 — backend)

- `workspace_drafts` table (additive migration `0015`): id, project_id, chapter_id, base_revision_id, `content_json` (per stable source segment id), revision, timestamps, `UNIQUE(project_id, chapter_id, base_revision_id)` — matching contract C03.
- `modules/translation/drafts.py`:
  - `save_draft(chapter, base_revision, content, expected_revision)` uses **compare-and-swap**: create only when no draft and expectation is None/0; otherwise the expectation must equal the stored revision and the write bumps it; any stale/absent expectation raises `DraftConflict("DRAFT_REVISION_CONFLICT")` **without overwriting**;
  - `restore_draft(...)` returns the stored draft (or None);
  - validation: content keys must be existing source-segment ids of the base revision (`DRAFT_SEGMENT_UNKNOWN`), values must be strings (`DRAFT_TEXT_INVALID`), payload capped at 1 MiB (`DRAFT_TOO_LARGE`), and the base revision must belong to the chapter (`BASE_REVISION_NOT_FOUND`);
  - drafts are written only to `workspace_drafts` — approved runs/segments and chapter approval pointers are untouched (tested).
- API: `GET /api/chapters/{chapter_id}/draft?baseRevisionId=…` (returns `{draft: ...|null}`) and `PUT /api/chapters/{chapter_id}/draft` with `{base_revision_id, content, expected_revision}` → 200 draft, 409 `DRAFT_REVISION_CONFLICT`, 400 for validation errors. The existing job draft snapshot/stream routes were kept (router prefix moved to explicit full paths).
- Tests: 5 service tests (create/restore round-trip, stale expectation rejected without overwrite, create-conflicts-when-exists, unknown/invalid payload + wrong base revision, approved translation untouched) and 1 API test (empty → create → restore → 409 conflict → 400 unknown segment).

## Remaining for U04 acceptance (later rounds)

- Editor wiring: restore draft into the translation editor, send `expectedRevision` on save, surface the 409 conflict payload/diff and keep unsaved content on save failure; idempotent retry of a save; and (for J04 round 4) let the worker persist provider deltas into this draft so `/draft/stream` can serve live offsets across processes.

## Validation (round 1)

- `backend/tests/translation/test_drafts.py backend/tests/api/test_draft_api.py backend/tests/db/test_schema.py -q` — 23 passed (incl. schema/migration round-trip).
- `backend/tests/api/test_draft_api.py backend/tests/jobs/test_draft_service.py -q` — 7 passed.
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 660 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed.
