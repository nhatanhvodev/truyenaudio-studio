# Task 23 / J04 report — rounds 1–3 (PARTIAL)

Status: PARTIAL — stream engine + snapshot + SSE draft feed done; live provider-delta SSE remains (Phase 1 adapters are request/response).

## Delivered (round 3)

- `draft_service.draft_frames(session, job_id, max_frames=1000)` — stored draft as ordered `(offset, text)` frames bounded to the G-PERF SSE store cap, used by the feed so a reconnect resumes by offset id without re-running the provider.
- `GET /api/jobs/{job_id}/draft/stream` (SSE): honours `Last-Event-ID`/`afterOffset`; emits `draft` events with offset ids, a `gap` snapshot event when the client cursor is not a known frame boundary, and a final `terminal` event carrying `status` + `approvable: false`.
- SSE event construction extracted to the pure `build_stream_events(frames, snapshot, cursor)` so the feed semantics are unit-tested deterministically (the in-process TestClient cannot host multiple sse-starlette responses in one test session — its EventSourceResponse binds an anyio event to a single loop — so the endpoint is exercised through the pure builder plus the non-SSE snapshot endpoint test).
- Tests added (4): frames order/offsets, sequential events + terminal payload, resume from a boundary cursor, gap snapshot for an unknown cursor. Draft suite now 13 tests.

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

## Remaining for J04 acceptance (round 4 — dependency noted)

- Wiring **live** provider deltas into the job-scoped SSE feed needs a persisted draft between the worker process (which would run `QwenMtAdapter.stream_translate`, now available) and the API process that serves `/draft/stream`. That persistence is the `workspace_drafts` table owned by **U04** (draft API + optimistic concurrency, M5). Until U04 lands, the feed serves the stored-segment draft (segmentReady path) and the snapshot endpoint, and reconnects still never re-run the provider.
- Remaining acceptance items therefore: adapter-delta -> draft checkpoint -> SSE offsets (U04 dependency), plus offset/attempt dedup and terminal flush/cancel propagation over that persisted store. The engine (`draft_stream`), the SSE event builder, and the adapter delta source are already implemented and tested.

## Validation (rounds 1–3)

- `backend/tests/jobs/test_draft_stream.py backend/tests/jobs/test_draft_service.py -q` — 13 passed.
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 654 passed after the streaming slice, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed (main.py carries only the repo-wide pre-existing E402 pattern); no provider/cloud call added.
