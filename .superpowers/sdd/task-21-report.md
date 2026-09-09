# Task 21 / J02 report — round 1 (PARTIAL)

Status: PARTIAL — persisted circuit breaker done; retry/cancel re-quote wiring under worker tracked as round 2.

## Delivered (round 1)

- `breaker_states` table (additive migration `0014`) + `BreakerState` model: per scope-key (provider/profile/model) state with consecutive failures, opened_at, cooldown_until.
- `modules/execution/breaker.py`: `CircuitBreaker(session, scope_key, threshold=5, cooldown_seconds=60, clock, id_factory)`:
  - `allow()` raises `BreakerOpen` (code `PROVIDER_CIRCUIT_OPEN`) while OPEN inside cooldown; after cooldown the next probe flips to HALF_OPEN and is allowed once; a second allow while HALF_OPEN is rejected (single probe per cycle).
  - `record_failure()` increments and opens/reopens at threshold or on a half-open failure; `record_success()` resets to CLOSED.
  - State is DB-persisted and read by later instances (survives worker restarts).
- Tests (4): close→open at threshold, cooldown→half-open→success closes, half-open failure reopens, persistence across instances.

## Remaining for J02 acceptance (round 2)

- Wire breaker + retry policy into the worker/execution dispatch (shared persisted policy, Retry-After/deadline, cancel safe point p95 ≤5 s at checkpoint, billingUnknown never resend/fallback) with fault-matrix tests; re-quote/re-guard on fallback.

## Validation (round 1)

- `backend/tests/execution/test_circuit_breaker.py backend/tests/db/test_schema.py -q` — 22 passed (incl. schema parity + migration round-trip).
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 621 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed.
- No provider/cloud call added.
