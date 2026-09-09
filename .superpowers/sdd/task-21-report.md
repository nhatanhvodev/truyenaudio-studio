# Task 21 / J02 report — rounds 1–2a (PARTIAL)

Status: PARTIAL — persisted breaker + dispatch gate helper wired into the cloud TRANSLATE handler; retry-policy fault matrix under worker remains.

## Delivered (round 1)

- `breaker_states` table (additive migration `0014`) + `BreakerState` model: per scope-key state with consecutive failures, opened_at, cooldown_until.
- `modules/execution/breaker.py` `CircuitBreaker`: CLOSED→OPEN at threshold (`PROVIDER_CIRCUIT_OPEN`), HALF_OPEN single probe after cooldown, success closes/failure reopens, state survives restarts.
- Tests (4): open-at-threshold, cooldown→half→close, half-fail reopen, persistence.

## Delivered (round 2a)

- `with_breaker(session, scope_key, fn, ...)`: dispatch gate that rejects before invoking `fn` while open and records success/failure; used by the worker TRANSLATE handler's qwen/gemini cloud paths (`scope provider:<p>:profile:<id>`); local fake path stays breaker-free (deterministic local).
- Tests added: gate counts failures then raises `BreakerOpen` without further `fn` calls; success closes the circuit (total 6 breaker tests).

## Remaining for J02 acceptance

- Retry policy at the worker boundary (Retry-After/deadline/attempt cap, cancel safe-point p95 at checkpoint, `billingUnknown` never resend/fallback) with a fault matrix; re-quote/re-guard on fallback.

## Validation

- `backend/tests/execution/test_circuit_breaker.py backend/tests/execution backend/tests/jobs -q` — 80 passed.
- Full backend suite (repo root): `D:\truyenaudio-studio\.venv\Scripts\python.exe -m pytest backend/tests -q` — 623 passed, 1 warning (pre-existing SQLAlchemy FK-cycle sort warning), exit 0.
- Changed-file Ruff passed.
- No provider/cloud call added.
