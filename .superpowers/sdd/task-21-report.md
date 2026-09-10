# Task 21 / J02 report — final

Status: DONE (code path + fault matrix verified; cancel p95 timing deferred to G-PERF benchmarks)

## Delivered

- Persisted circuit breaker (round 1): `breaker_states` (migration `0014`), `CircuitBreaker` CLOSED/OPEN/HALF_OPEN per provider/profile/model key, half-open single probe after cooldown, survives restarts.
- Dispatch gate (round 2a): `with_breaker(...)` used by the worker TRANSLATE handler for qwen/gemini cloud paths; local fake path unaffected.
- Retry classification alignment (round 2b): `classify_retry` now maps shared transport codes — `HTTP_429` obeys `Retry-After` (capped at 600 s) and falls back to the shared backoff policy when no header is present; `HTTP_408`/`HTTP_5xx`/`PROVIDER_TIMEOUT`/`PROVIDER_NETWORK`/`DB_BUSY` retry with the 2/10/30 s policy under the attempt cap; `HTTP_400/401/403/404` and other codes never retry (no 401 loop).

## Acceptance mapping

- 429 obey Retry-After ✓ (header path + cap test; header-less fallback keeps retry under policy).
- 401 không loop ✓ (`HTTP_401` -> FAIL tests; runner `fail` only retries when `error.retryable` AND decision RETRY).
- billingUnknown chặn resend/fallback ✓ — existing runner tests: cloud-sent expired attempts -> `BILLING_UNKNOWN`, never auto-retried; no fallback path exists in Phase 1 (explicit no-silent-fallback policy), so fallback re-quote/re-guard is not applicable yet.
- Breaker persist/half-open ✓ + wired gate.
- Fault matrix timeout/5xx/429/cancel/lease/restart/half-open ✓ — runner suite covers timeout-after-send, lease/restart recovery, cancel requested/acknowledge; new HTTP matrix; breaker half-open tests.
- Cancel p95 ≤5 s at checkpoint is a **G-PERF benchmark row** (V01 harness), not a unit gate; measured later on the target machine.

## Validation

- `backend/tests/jobs/test_runner.py -q` — 52 passed (incl. 3 new HTTP retry-classification tests).
- `backend/tests/execution backend/tests/jobs -q` — 80 passed (round 2a numbers).
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 626 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed; no provider/cloud call added.
