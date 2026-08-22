from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import Usage
from app.db.models import ProviderProfile, UsageLedger


class QuotaUnavailable(ValueError):
    """Raised when a cloud TTS profile has no explicit quota policy."""


@dataclass(frozen=True)
class QuotaEvaluation:
    billable_usage: tuple[Usage, ...]
    remaining_quota: tuple[Usage, ...]


def evaluate_profile_quota(
    session: Session,
    profile: ProviderProfile,
    usage: tuple[Usage, ...],
    *,
    provider: str,
    now: datetime,
    require_config: bool,
) -> QuotaEvaluation:
    config = profile.config_json or {}
    quota_rows = config.get("quota")
    if require_config and quota_rows is None:
        raise QuotaUnavailable("PROVIDER_QUOTA_MISSING")
    if not isinstance(quota_rows, list):
        quota_rows = []

    month = now.strftime("%Y-%m")
    configured_by_unit = _configured_by_unit(quota_rows, month)
    consumed_by_unit = _consumed_by_unit(
        session,
        provider=provider,
        model=profile.model or "",
        region=profile.region,
        month=month,
    )
    billable: list[Usage] = []
    remaining: list[Usage] = []
    for item in usage:
        configured = configured_by_unit.get(item.unit, 0)
        consumed = consumed_by_unit.get(item.unit, 0)
        before_call_remaining = max(0, configured - consumed)
        billable_units = max(0, item.measured_units - before_call_remaining)
        remaining.append(Usage(item.unit, before_call_remaining, f"quota:{month}"))
        billable.append(Usage(item.unit, billable_units, item.provider_request_id))
    return QuotaEvaluation(tuple(billable), tuple(remaining))


def _configured_by_unit(rows: list[object], month: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("month") or month) != month:
            continue
        unit = str(row.get("unit") or "")
        if not unit:
            continue
        configured_units = row.get("configured_units", 0)
        if type(configured_units) is not int or configured_units < 0:
            configured_units = 0
        result[unit] = result.get(unit, 0) + configured_units
    return result


def _consumed_by_unit(
    session: Session,
    *,
    provider: str,
    model: str,
    region: str | None,
    month: str,
) -> dict[str, int]:
    rows = session.scalars(
        select(UsageLedger).where(
            UsageLedger.provider == provider,
            UsageLedger.model == model,
            UsageLedger.region == region,
        )
    ).all()
    result: dict[str, int] = {}
    for row in rows:
        if not getattr(row.created_at, "strftime", None):
            continue
        if row.created_at.strftime("%Y-%m") != month:
            continue
        result[row.unit] = result.get(row.unit, 0) + row.measured_units
    return result
