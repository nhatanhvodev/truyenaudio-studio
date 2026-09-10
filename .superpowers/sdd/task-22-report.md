# Task 22 / J03 report — rounds 1–2 (PARTIAL)

Status: PARTIAL — feed infrastructure + retention purge + rebuild parity done; emit-in-transaction at main writers remains.

## Delivered (round 2)

- `modules/events/retention.py`:
  - `purge_events_before(session, cutoff)`: deletes feed rows older than an **explicit caller-provided cutoff** (no retention window invented here); source tables untouched so the projection can be rebuilt.
  - `rebuild_feed(session)`: re-syncs the feed from jobs/audit/usage with dedupe (idempotent; second call adds 0), returning newly added rows — projection rebuild parity after a purge.
- Tests (3): idempotent rebuild, purge only before cutoff, rebuild restores purged projection.

## Delivered / verified (round 1)

- Existing event feed architecture reviewed and confirmed by tests:
  - `EventLog` append-only with autoincrement `sequence_id` + `(entity_type, entity_id, sequence_id)` index; `created_at` only (payload synthesized at read).
  - `/api/events` SSE with cursor (`after`/`Last-Event-ID`), `_events(...)` filters `sequenceId > cursor`.
  - `_sync_event_log` backfills job/audit/usage rows into the feed with race-safe dedupe (append-only constraint; commits are no-op when nothing changed) — previously hardened by `d577b18`/`deb91f3` and covered by `tests/db/test_event_log_append_only.py`.
  - Diagnostics logging/service modules exist with redaction helpers (used by S02 tests).
- Acceptance mapping (partial): indexed cursor query ✓; replay/filter stream ✓; structured redaction on diagnostics/audit ✓; snapshot/rebuild + retention purge ✓ (round 2, explicit cutoff); per-transaction emit at each main writer ✗ (feed is synthesized from tables at read instead).

## Remaining for J03 acceptance (next rounds)

- Emit transitions in the same transaction as the source change at the main writers (jobs/translation/speech/export) without breaking the append-only dedupe; optional audit-vs-draft retention windows remain caller policy.

## Validation (rounds 1–2)

- `backend/tests/events -q` — 3 passed (round 2); `backend/tests/db/test_event_log_append_only.py` green in full suite.
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 629 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- No provider/cloud call added.
