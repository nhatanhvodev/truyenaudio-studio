# Task 22 / J03 report — round 1 (PARTIAL)

Status: PARTIAL — event feed infrastructure verified; retention/projection parity + emit-in-transaction writers remain.

## Delivered / verified (round 1)

- Existing event feed architecture reviewed and confirmed by tests:
  - `EventLog` append-only with autoincrement `sequence_id` + `(entity_type, entity_id, sequence_id)` index; `created_at` only (payload synthesized at read).
  - `/api/events` SSE with cursor (`after`/`Last-Event-ID`), `_events(...)` filters `sequenceId > cursor`.
  - `_sync_event_log` backfills job/audit/usage rows into the feed with race-safe dedupe (append-only constraint; commits are no-op when nothing changed) — previously hardened by `d577b18`/`deb91f3` and covered by `tests/db/test_event_log_append_only.py`.
  - Diagnostics logging/service modules exist with redaction helpers (used by S02 tests).
- Acceptance mapping (partial): indexed cursor query ✓; replay/filter stream ✓; structured redaction on diagnostics/audit ✓ (secret/source not in feed payload); snapshot/rebuild & retention ✗; per-transaction emit at each main writer ✗ (feed is synthesized from tables at read instead).

## Remaining for J03 acceptance (next rounds)

- Retention policy + audit purge (distinct from draft feed) with tests; projection rebuild parity test; decide/emit transitions in the same transaction as the source change at the main writers (jobs/translation/speech/export) without breaking the append-only dedupe.

## Validation (round 1)

- `backend/tests/db/test_event_log_append_only.py` green (included in full backend suite).
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 626 passed (after J02 close), 1 warning (pre-existing SQLAlchemy FK-cycle sort warning).
- No provider/cloud call added.
