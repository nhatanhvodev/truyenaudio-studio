# Task 23 / J04 report — rounds 1–2 (PARTIAL)

Status: PARTIAL — stream engine + persisted-draft snapshot endpoint done; live provider-delta SSE remains (Phase 1 adapters are request/response).

## Delivered (round 2)

- `modules/translation/draft_service.py`: `load_draft_stream(session, job_id)` rebuilds the draft stream from the chapter run's stored segments as `segmentReady` frames (ordinal order, finished unless the run is still RUNNING); `draft_snapshot(session, job_id, after_offset)` returns offset/text/`resync`/status/truncated/`approvable=False`.
- `/api/jobs/{job_id}/draft?afterOffset=N` router (new `api/drafts.py`, registered in the app) returning the reconnect snapshot; unknown job -> 404.
- Tests (2): snapshot replays stored segments with correct offset/resync/never-approvable; endpoint returns the snapshot and 404s for an unknown job. Combined with round 1: 9 draft tests.

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

- Wire live provider deltas to job-scoped SSE (offset/attempt events, gap snapshot delivery, terminal flush and cancel propagation). Phase 1 adapters are request/response (qwen-mt-flash incremental streaming is not implemented in the adapter), so end-to-end delta evidence is pending that work; non-stream drafts already work through `segmentReady` + the snapshot endpoint.

## Validation (rounds 1–2)

- `backend/tests/jobs/test_draft_stream.py backend/tests/jobs/test_draft_service.py -q` — 9 passed.
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 643 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed (main.py carries only the repo-wide pre-existing E402 pattern); no provider/cloud call added.
