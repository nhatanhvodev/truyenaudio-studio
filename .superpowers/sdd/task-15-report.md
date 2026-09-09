# Task 15 / C04 report

Status: DONE

## Delivered

- New schema (additive migration `0011`): `characters` (stable identity + project), `character_revisions` (immutable fact rows: canonical_name, aliases JSON, entity_type, role nullable, gender nullable, status, evidence_source_revision_id -> source_revisions, evidence_segment_ids JSON; UNIQUE(character_id, revision_no); supersedes chain), `character_relationships` (directed from->to with ordinal interval, addressing JSON, evidence_source_revision_id, status ACTIVE; check to_ordinal NULL or from<=to; (project_id, from_ordinal) index).
- `characters` service:
  - create/revise candidate (create_or_revise): validation for entity type / gender / role / alias normalization; gender stays NULL when absent (never guessed). Editing creates a new revision.
  - `approve(project, character, evidence...)` — evidence is mandatory: a source revision belonging to the project plus at least one source segment under it (`CHARACTER_EVIDENCE_REQUIRED` / `_SOURCE_NOT_FOUND` / `_SEGMENTS_INVALID` otherwise); approval creates a new APPROVED revision superseding the active one.
  - `resolve_alias`: casefolded match over canonical name + aliases of active revisions; ambiguity across several characters raises `CHARACTER_ALIAS_AMBIGUOUS` (409 via API) — characters are never merged by name.
  - relationships: `add_relationship` validates same project (`CHARACTER_NOT_FOUND` cross-project), from != to, ordinal bounds, both characters APPROVED, addressing as a string->string map; `active_relationships(project, ordinal)` returns relations whose interval covers the chapter ordinal (to_ordinal NULL = open ended).
  - Character is kept separate from VoiceRole (no audio mapping here).
- `/api/projects/{id}/characters` router (GET list, POST candidate, POST `/{character_id}/approve`, GET resolve, GET/POST relationships) registered in the app.

## Design notes / residual (visible follow-ups)

- Editing an APPROVED character via create_or_revise produces a new CANDIDATE revision (approval state is not carried over implicitly; re-approval needs evidence), matching the candidate/approve lifecycle. Context/QA wiring that consumes resolved characters and per-chapter addressing is the C05 context-engine step.
- Relationships are not separately versioned (rows are immutable facts; edits create new rows). Status column kept for future stale marking when a character revision is superseded.

## Validation

- `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests/translation/test_characters.py backend/tests/translation/test_characters_api.py backend/tests/db/test_schema.py -q` — green (7 service/scope tests + 1 API test + schema parity/round-trip).
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 593 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed (models, service, api, migration, tests); `app/main.py` carries only the repo-wide pre-existing E402 pattern (30 total at HEAD+this task).
- Migration 0011 upgrade/downgrade exercised by schema round-trip tests.
