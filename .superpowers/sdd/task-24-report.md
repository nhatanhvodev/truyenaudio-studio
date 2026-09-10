# Task 24 / P05 stream slice — report

Status: DONE (adapter-level incremental streaming; wiring into the J04 SSE feed tracked there)

## Delivered

- `QwenMtAdapter.stream_translate(request) -> AsyncIterator[str]` — DashScope incremental SSE mode per the official Qwen-MT docs read earlier this session:
  - request adds `parameters.incremental_output = true` and sends `X-DashScope-SSE: enable` + `Accept: text/event-stream` with the shared bearer JSON headers (no `system`/chat wrapper);
  - the stream is parsed with the shared `IncrementalSseParser` (P03), so UTF-8/JSON split across chunks is handled; `[DONE]` frames are ignored; the final `usage`/`request_id` frame is captured into `last_stream_usage`;
  - both DashScope streaming modes are supported: incremental models (flash/lite) send only new text, while plus/turbo send the full text so far — the mode is detected from the frames and cumulative frames are de-duplicated into deltas; a regression inside cumulative mode is reported as `MALFORMED_STREAM`;
  - guard/validation (dispatch authorization, consent/budget, source limits, endpoint/model validation) run **before** any bytes are sent; non-200 uses the shared `normalize_http_error` (`HTTP_503` fixture), transport failures after dispatch map to `ProviderBillingUnknown` when billing is unknown, and a truncated frame stream is reported by `parser.finish()` (`TRUNCATED_STREAM`).
- Tests (7, `tests/providers/test_qwen_mt_stream.py`): incremental deltas + usage/request id + header/body contract, non-incremental de-duplication, UTF-8 split across chunks, cumulative regression -> MALFORMED_STREAM, non-200 -> HTTP_503, truncated stream, and guard blocking before any request.

## Notes / residual

- The adapter now provides the delta source for the J04 draft stream; wiring `stream_translate` into the job-scoped SSE feed (offset/attempt events) remains J04 round 4.
- No live provider call was made; all fixtures are deterministic fakes.

## Validation

- `backend/tests/providers/test_qwen_mt_stream.py backend/tests/providers/test_qwen_mt_contract.py -q` — 28 passed (combined), stream file alone 7 passed.
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 654 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed.
