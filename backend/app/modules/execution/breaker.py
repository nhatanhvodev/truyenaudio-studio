"""Persisted circuit breaker for provider dispatch (task J02).

State is kept in ``breaker_states`` per scope key (provider/profile/model) so
the breaker survives worker restarts. Closed -> after ``threshold`` consecutive
failures -> OPEN (dispatches rejected) -> after ``cooldown_seconds`` the next
probe transitions to HALF_OPEN and allows exactly one trial; a success closes
the breaker, a failure reopens it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import new_id
from app.db.base import utc_now
from app.db.models import BreakerState


STATE_CLOSED = "CLOSED"
STATE_OPEN = "OPEN"
STATE_HALF_OPEN = "HALF_OPEN"


class BreakerOpen(Exception):
    def __init__(self, scope_key: str) -> None:
        self.code = "PROVIDER_CIRCUIT_OPEN"
        super().__init__(f"circuit open for {scope_key}")


class CircuitBreaker:
    def __init__(
        self,
        session: Session,
        scope_key: str,
        *,
        threshold: int = 5,
        cooldown_seconds: int = 60,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] = new_id,
    ) -> None:
        self.session = session
        self.scope_key = scope_key
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock or (lambda: datetime.now(UTC))
        self.id_factory = id_factory

    def allow(self) -> None:
        """Raise BreakerOpen when the circuit is open (no dispatch allowed)."""
        row = self._load()
        if row is None:
            return
        if row.state == STATE_CLOSED:
            return
        if row.state == STATE_OPEN:
            if row.cooldown_until is not None and self.clock() < _parse(row.cooldown_until):
                raise BreakerOpen(self.scope_key)
            row.state = STATE_HALF_OPEN
            row.updated_at = utc_now()
            self.session.flush()
            return
        # HALF_OPEN allows a single probe per cycle; subsequent calls wait.
        if row.state == STATE_HALF_OPEN:
            raise BreakerOpen(self.scope_key)

    def record_failure(self) -> None:
        now = self.clock()
        row = self._ensure_row()
        row.consecutive_failures = (row.consecutive_failures or 0) + 1
        if row.state == STATE_HALF_OPEN or row.consecutive_failures >= self.threshold:
            row.state = STATE_OPEN
            row.opened_at = now
            row.cooldown_until = now + timedelta(seconds=self.cooldown_seconds)
        row.updated_at = utc_now()
        self.session.flush()

    def record_success(self) -> None:
        row = self._ensure_row()
        row.consecutive_failures = 0
        row.state = STATE_CLOSED
        row.opened_at = None
        row.cooldown_until = None
        row.updated_at = utc_now()
        self.session.flush()

    def snapshot(self) -> dict[str, object]:
        row = self._load()
        if row is None:
            return {"state": STATE_CLOSED, "consecutive_failures": 0}
        return {
            "state": row.state,
            "consecutive_failures": row.consecutive_failures,
            "opened_at": row.opened_at,
            "cooldown_until": row.cooldown_until,
        }

    def _load(self) -> BreakerState | None:
        return self.session.scalar(
            select(BreakerState).where(BreakerState.scope_key == self.scope_key)
        )

    def _ensure_row(self) -> BreakerState:
        row = self._load()
        if row is None:
            row = BreakerState(
                id=self.id_factory(),
                scope_key=self.scope_key,
                state=STATE_CLOSED,
                consecutive_failures=0,
                updated_at=utc_now(),
            )
            self.session.add(row)
            self.session.flush()
        return row


def _parse(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value)).astimezone(UTC)
