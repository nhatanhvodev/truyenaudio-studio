# Task 23 / J04 report — round 1 (PARTIAL)

Status: PARTIAL — resumable bounded draft stream engine + tests done; provider-delta/SSE wiring remains.

## Delivered (round 1)

- `modules/translation/draft_stream.py` — `DraftStream` with `DeltaFrame`/`ApplyResult`:
  - monotonic offsets: applied frames advance `offset`/`text`; a replayed frame (same/earlier offset) returns `duplicate` and never re-appends; frames from a **stale attempt** are also ignored so committed draft text cannot be rewritten;
  - non-contiguous offset records a **gap** (`gaps`), does not apply, and returns the current snapshot text for client resync;
  - `snapshot(after_offset)` supports reconnect: returns server offset/text and a `resync` flag when the client is ahead — reconnecting never re-runs the provider (provider invocation stays outside the stream);
  - terminal/cancel: `finish()`/`cancel()` are race-safe (`DraftStreamClosed` on later frames); a canceled stream **keeps the partial draft for viewing**;
  - memory/frame bounded: `max_frames` (default 1000 metadata frames, matching the G-PERF SSE store cap) and `max_chars` (default 200 000) with a `truncated` flag;
  - `apply_segment(...)` covers the non-stream provider path (segmentReady = one frame); `is_approvable` is always False (a draft is never approved output).
- Tests (7): split deltas/order, duplicate + stale attempt, gap + resync + recovery, reconnect snapshot, cancel/finish race, bounded frames/chars, segmentReady + never-approvable.

## Remaining for J04 acceptance (next rounds)

- Wire the stream to the provider delta path and the SSE endpoint (job-scoped draft events with offset/attempt, gap snapshot delivery, terminal flush and cancel propagation) — Phase 1 adapters are request/response (qwen-mt-flash incremental streaming is not implemented in the adapter), so end-to-end streaming evidence is pending that work; partial drafts keep working via `segmentReady`/progress events.

## Validation (round 1)

- `backend/tests/jobs/test_draft_stream.py -q` — 7 passed.
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 641 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed; no provider/cloud call added.
- No provider/cloud call added.
