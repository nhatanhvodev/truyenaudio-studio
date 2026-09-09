from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.execution.breaker import BreakerOpen, CircuitBreaker


NOW = datetime(2026, 8, 19, 4, 0, tzinfo=UTC)
KEY = "provider:qwen:model:qwen-mt-plus:region:frankfurt"


def _breaker(db_session, *, threshold: int = 3, cooldown_seconds: int = 60) -> CircuitBreaker:
    return CircuitBreaker(
        db_session,
        KEY,
        threshold=threshold,
        cooldown_seconds=cooldown_seconds,
        clock=lambda: NOW,
    )


def test_closed_circuit_allows_and_opens_after_threshold(db_session) -> None:
    breaker = _breaker(db_session)
    breaker.allow()
    breaker.record_failure()
    breaker.record_failure()
    breaker.allow()
    breaker.record_failure()

    snapshot = breaker.snapshot()
    assert snapshot["state"] == "OPEN"
    assert snapshot["consecutive_failures"] == 3
    with pytest.raises(BreakerOpen):
        breaker.allow()


def test_open_circuit_half_opens_after_cooldown_and_probe_closes(db_session) -> None:
    breaker = _breaker(db_session)
    for _ in range(3):
        breaker.record_failure()

    with pytest.raises(BreakerOpen):
        breaker.allow()

    later = CircuitBreaker(
        db_session,
        KEY,
        threshold=3,
        cooldown_seconds=60,
        clock=lambda: NOW + timedelta(seconds=61),
    )
    later.allow()  # transitions to HALF_OPEN and permits the probe
    later.record_success()

    assert later.snapshot()["state"] == "CLOSED"
    assert later.snapshot()["consecutive_failures"] == 0


def test_half_open_failure_reopens_circuit(db_session) -> None:
    breaker = _breaker(db_session)
    for _ in range(3):
        breaker.record_failure()
    probe = CircuitBreaker(
        db_session,
        KEY,
        threshold=3,
        cooldown_seconds=60,
        clock=lambda: NOW + timedelta(seconds=61),
    )
    probe.allow()
    probe.record_failure()

    assert probe.snapshot()["state"] == "OPEN"
    assert probe.snapshot()["cooldown_until"] is not None


def test_breaker_state_persists_across_instances(db_session) -> None:
    first = _breaker(db_session)
    for _ in range(3):
        first.record_failure()

    fresh = CircuitBreaker(db_session, KEY, clock=lambda: NOW)
    with pytest.raises(BreakerOpen):
        fresh.allow()
