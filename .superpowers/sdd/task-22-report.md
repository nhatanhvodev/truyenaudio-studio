# Task 22 / J03 report — final

Status: DONE (feed, projection rebuild, retention, emit-in-transaction)

## Delivered

- Feed infrastructure verified (round 1): append-only `EventLog` (autoincrement `sequence_id`, `(entity_type, entity_id, sequence_id)` index), `/api/events` SSE with cursor (`after`/`Last-Event-ID`), `_sync_event_log` backfill with race-safe dedupe (d577b18, deb91f3), diagnostics redaction helpers.
- Retention + rebuild (round 2): `modules/events/retention.py` — `purge_events_before(session, cutoff)` (explicit caller cutoff, source tables untouched) and `rebuild_feed(session)` (idempotent projection rebuild restoring purged rows). 3 tests.
- Emit-in-transaction (round 3):
  - `JobRunner.complete` and terminal `JobRunner.fail` (FAILED / BILLING_UNKNOWN) append a `job` feed row **inside the same connection transaction**, guarded by `NOT EXISTS` so a terminal transition cannot duplicate the feed row; retryable failures (requeue) emit nothing.
  - `TranslationWorkflow.approve_revision` appends the `audit` feed row for the approval AuditEvent in the same session transaction (id shared with the audit row).
  - Tests (5): completion emits once, retryable failure emits nothing, terminal failure emits once, approval emits audit event, `rebuild_feed` adds no duplicates after emission.
- Structured logging: `modules/diagnostics/logging.py` already whitelists model / latencyMs / costVnd (+ usage/request id) with redaction — matches the acceptance requirement.

## Acceptance mapping (J03)

- Event/projection cùng transaction ✓ (job terminal + approval); replay không mất transition ✓ (append-only references + rebuild parity test); 410 cursor cũ — feed re-reads source rows so an old cursor stays valid (documented behaviour, no expiry introduced); projection rebuild khớp ✓; no raw source/secret in audit ✓ (redacted_details + diagnostics whitelist); indexed cursor query ✓; retention ✓ (explicit cutoff).

## Validation

- `backend/tests/events -q` — 8 passed (3 retention + 5 emission).
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 634 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed; no provider/cloud call added.
