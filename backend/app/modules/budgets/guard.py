from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import Usage, new_id
from app.db.models import BudgetAuthorization as BudgetAuthorizationRow
from app.db.models import RateCard, UsageLedger


FX_RATE_VND_PER_USD = 26_500
CONTINGENCY_PERCENT = 15
REGULAR_CAP_VND = 400_000
QA_REPAIR_RESERVE_VND = 50_000
RERENDER_RESERVE_VND = 50_000
HARD_LIMIT_VND = 500_000
WARNING_THRESHOLD_VND = 350_000
QUOTE_TTL_MINUTES = 10
RESERVE_LIMITS = {
    "QA_REPAIR": QA_REPAIR_RESERVE_VND,
    "RERENDER": RERENDER_RESERVE_VND,
}


@dataclass(frozen=True)
class CostQuote:
    id: str
    operation_id: str
    estimate_vnd: int
    contingency_vnd: int
    category: str
    rate_card_id: str | None
    expires_at: datetime
    rate_card_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    provider_profile_id: str | None = None
    provider_profile_revision: int | None = None
    cloud_consent_id: str | None = None
    stage: str | None = None
    plan_hash: str | None = None
    quote_hash: str | None = None

    @property
    def total_vnd(self) -> int:
        return self.estimate_vnd + self.contingency_vnd


@dataclass(frozen=True)
class BudgetAuthorization:
    id: str
    operation_id: str
    estimate_vnd: int
    contingency_vnd: int
    expires_at: datetime
    status: str
    category: str | None = None
    rate_card_ids: tuple[str, ...] = ()
    provider_profile_id: str | None = None
    provider_profile_revision: int | None = None
    cloud_consent_id: str | None = None
    stage: str | None = None
    plan_hash: str | None = None
    quote_hash: str | None = None


class BudgetBlocked(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BudgetGuard:
    def __init__(self, session: Session, *, now: Callable[[], datetime] | None = None) -> None:
        self.session = session
        self._now = now

    def quote(self, operation_id: str, estimate_vnd: int, category: str) -> CostQuote:
        _require_non_negative_int(estimate_vnd, "estimate_vnd")
        contingency_vnd = _contingency(estimate_vnd)
        return CostQuote(
            id=new_id(),
            operation_id=operation_id,
            estimate_vnd=estimate_vnd,
            contingency_vnd=contingency_vnd,
            category=category,
            rate_card_id=None,
            rate_card_ids=(),
            expires_at=self.now() + timedelta(minutes=QUOTE_TTL_MINUTES),
            warnings=self._warnings_for(estimate_vnd + contingency_vnd),
        )

    def quote_usage(
        self,
        *,
        operation_id: str,
        provider: str,
        model: str,
        region: str | None,
        usage: tuple[Usage, ...],
        category: str,
        provider_profile_id: str | None = None,
        provider_profile_revision: int | None = None,
        cloud_consent_id: str | None = None,
        stage: str | None = None,
        plan_hash: str | None = None,
    ) -> CostQuote:
        if not usage:
            raise BudgetBlocked("BUDGET_USAGE_REQUIRED")

        estimate_vnd = 0
        rate_card_ids: list[str] = []
        for item in usage:
            _require_non_negative_int(item.measured_units, "measured_units")
            rate_card = self._current_rate_card(provider, model, region, item.unit)
            if rate_card is None:
                raise BudgetBlocked("BUDGET_RATE_MISSING")
            rate_card_ids.append(rate_card.id)
            usd_micros = _usd_micros(rate_card.price_usd_micros_per_million_units, item.measured_units)
            estimate_vnd += _vnd_from_usd_micros(usd_micros)

        expires_at = self.now() + timedelta(minutes=QUOTE_TTL_MINUTES)
        quote_hash = _quote_hash(
            operation_id=operation_id,
            estimate_vnd=estimate_vnd,
            contingency_vnd=_contingency(estimate_vnd),
            category=category,
            rate_card_ids=tuple(rate_card_ids),
            provider_profile_id=provider_profile_id,
            provider_profile_revision=provider_profile_revision,
            cloud_consent_id=cloud_consent_id,
            stage=stage,
            plan_hash=plan_hash,
        )
        return CostQuote(
            id=new_id(),
            operation_id=operation_id,
            estimate_vnd=estimate_vnd,
            contingency_vnd=_contingency(estimate_vnd),
            category=category,
            rate_card_id=rate_card_ids[0] if len(rate_card_ids) == 1 else None,
            rate_card_ids=tuple(rate_card_ids),
            expires_at=expires_at,
            warnings=self._warnings_for(estimate_vnd + _contingency(estimate_vnd)),
            provider_profile_id=provider_profile_id,
            provider_profile_revision=provider_profile_revision,
            cloud_consent_id=cloud_consent_id,
            stage=stage,
            plan_hash=plan_hash,
            quote_hash=quote_hash,
        )

    def authorize(self, quote: CostQuote) -> BudgetAuthorization:
        if quote.expires_at <= self.now():
            raise BudgetBlocked("BUDGET_QUOTE_EXPIRED")

        row = BudgetAuthorizationRow(
            id=quote.id,
            operation_id=quote.operation_id,
            estimate_vnd=quote.estimate_vnd,
            contingency_vnd=quote.contingency_vnd,
            category=quote.category,
            rate_card_ids_json=list(quote.rate_card_ids),
            provider_profile_id=quote.provider_profile_id,
            provider_profile_revision=quote.provider_profile_revision,
            cloud_consent_id=quote.cloud_consent_id,
            stage=quote.stage,
            plan_hash=quote.plan_hash,
            quote_hash=quote.quote_hash,
            expires_at=quote.expires_at,
            status="HELD",
        )
        self.session.add(row)
        self.session.flush()

        total_after = self._committed_vnd()
        reserve_limit = RESERVE_LIMITS.get(quote.category)
        if reserve_limit is not None and self._category_authorized_vnd(quote.category) > reserve_limit:
            self.session.rollback()
            raise BudgetBlocked(f"BUDGET_{quote.category}_RESERVE_EXCEEDED")
        if quote.category == "REGULAR" and total_after > REGULAR_CAP_VND:
            self.session.rollback()
            raise BudgetBlocked("BUDGET_REGULAR_CAP_EXCEEDED")
        if total_after > HARD_LIMIT_VND:
            self.session.rollback()
            raise BudgetBlocked("BUDGET_HARD_LIMIT_EXCEEDED")

        self.session.commit()
        return _authorization_view(row)

    def validate_authorization(self, authorization_id: str, operation_id: str) -> BudgetAuthorization:
        row = self.session.get(BudgetAuthorizationRow, authorization_id)
        if row is None or row.operation_id != operation_id or row.status not in {"HELD", "COMMITTED"}:
            raise BudgetBlocked("BUDGET_AUTHORIZATION_INVALID")
        if row.expires_at <= self.now():
            raise BudgetBlocked("BUDGET_AUTHORIZATION_EXPIRED")
        return _authorization_view(row)

    def validate_authorization_for_quote(self, authorization_id: str, quote: CostQuote) -> BudgetAuthorization:
        authorization = self.validate_authorization(authorization_id, quote.operation_id)
        if authorization.status != "HELD":
            raise BudgetBlocked("BUDGET_AUTHORIZATION_NOT_HELD")
        if authorization.category != quote.category or authorization.rate_card_ids != quote.rate_card_ids:
            raise BudgetBlocked("BUDGET_AUTHORIZATION_METADATA_MISMATCH")
        if authorization.estimate_vnd < quote.estimate_vnd or authorization.contingency_vnd < quote.contingency_vnd:
            raise BudgetBlocked("BUDGET_AUTHORIZATION_UNDERFUNDED")
        if (
            authorization.provider_profile_id != quote.provider_profile_id
            or authorization.provider_profile_revision != quote.provider_profile_revision
            or authorization.cloud_consent_id != quote.cloud_consent_id
            or authorization.stage != quote.stage
            or authorization.plan_hash != quote.plan_hash
            or authorization.quote_hash != quote.quote_hash
        ):
            raise BudgetBlocked("BUDGET_AUTHORIZATION_METADATA_MISMATCH")
        return authorization

    def commit_usage(
        self,
        authorization_id: str,
        *,
        provider: str,
        model: str,
        region: str | None,
        usage: tuple[Usage, ...],
    ) -> None:
        row = self.session.get(BudgetAuthorizationRow, authorization_id)
        if row is None:
            raise BudgetBlocked("BUDGET_AUTHORIZATION_INVALID")
        if row.status == "COMMITTED":
            return
        if row.status != "HELD":
            raise BudgetBlocked("BUDGET_AUTHORIZATION_NOT_HELD")
        if row.expires_at <= self.now():
            raise BudgetBlocked("BUDGET_AUTHORIZATION_EXPIRED")

        for item in usage:
            rate_card = self._current_rate_card(provider, model, region, item.unit)
            if rate_card is None:
                raise BudgetBlocked("BUDGET_RATE_MISSING")
            usd_micros = _usd_micros(rate_card.price_usd_micros_per_million_units, item.measured_units)
            self.session.add(
                UsageLedger(
                    id=new_id(),
                    provider=provider,
                    model=model,
                    region=region,
                    operation_id=row.operation_id,
                    unit=item.unit,
                    measured_units=item.measured_units,
                    rate_card_id=rate_card.id,
                    actual_usd_micros=usd_micros,
                    fx_rate=FX_RATE_VND_PER_USD,
                    actual_vnd=_vnd_from_usd_micros(usd_micros),
                    billing_confidence="CONFIRMED",
                )
            )
        row.status = "COMMITTED"
        self.session.commit()

    def release_authorization(self, authorization_id: str) -> None:
        row = self.session.get(BudgetAuthorizationRow, authorization_id)
        if row is None:
            raise BudgetBlocked("BUDGET_AUTHORIZATION_INVALID")
        if row.status == "HELD":
            row.status = "RELEASED"
            self.session.commit()

    def now(self) -> datetime:
        if self._now is not None:
            return self._now()
        from app.db.base import utc_now

        return utc_now()

    def _current_rate_card(self, provider: str, model: str, region: str | None, unit: str) -> RateCard | None:
        now = self.now()
        return self.session.scalar(
            select(RateCard)
            .where(
                RateCard.provider == provider,
                RateCard.model == model,
                RateCard.region == region,
                RateCard.unit == unit,
                RateCard.effective_from <= now,
                (RateCard.effective_to.is_(None) | (RateCard.effective_to > now)),
            )
            .order_by(RateCard.effective_from.desc())
        )

    def _committed_vnd(self) -> int:
        ledger_rows = self.session.scalars(select(UsageLedger)).all()
        ledger_by_operation = _ledger_by_operation(ledger_rows)
        authorization_rows = self.session.scalars(
            select(BudgetAuthorizationRow).where(BudgetAuthorizationRow.status.in_(("HELD", "COMMITTED")))
        ).all()
        counted_operations: set[str] = set()
        total = 0

        for row in authorization_rows:
            ledgers = ledger_by_operation.get(row.operation_id, ())
            if row.status == "HELD":
                if row.expires_at > self.now():
                    total += _authorization_total(row)
                continue
            counted_operations.add(row.operation_id)
            total += _committed_operation_total(row, ledgers)

        for row in ledger_rows:
            if row.operation_id not in counted_operations:
                total += row.actual_vnd or 0
        return total

    def _category_authorized_vnd(self, category: str) -> int:
        ledger_rows = self.session.scalars(select(UsageLedger)).all()
        ledger_by_operation = _ledger_by_operation(ledger_rows)
        rows = self.session.scalars(
            select(BudgetAuthorizationRow).where(
                BudgetAuthorizationRow.category == category,
                BudgetAuthorizationRow.status.in_(("HELD", "COMMITTED")),
            )
        ).all()
        total = 0
        for row in rows:
            if row.status == "HELD":
                if row.expires_at > self.now():
                    total += _authorization_total(row)
                continue
            total += _committed_operation_total(row, ledger_by_operation.get(row.operation_id, ()))
        return total

    def _warnings_for(self, quote_total_vnd: int) -> tuple[str, ...]:
        if self._committed_vnd() + quote_total_vnd > WARNING_THRESHOLD_VND:
            return ("BUDGET_WARNING_THRESHOLD_EXCEEDED",)
        return ()


def _authorization_view(row: BudgetAuthorizationRow) -> BudgetAuthorization:
    return BudgetAuthorization(
        id=row.id,
        operation_id=row.operation_id,
        estimate_vnd=row.estimate_vnd,
        contingency_vnd=row.contingency_vnd,
        expires_at=row.expires_at,
        status=row.status,
        category=row.category,
        rate_card_ids=tuple(row.rate_card_ids_json or ()),
        provider_profile_id=row.provider_profile_id,
        provider_profile_revision=row.provider_profile_revision,
        cloud_consent_id=row.cloud_consent_id,
        stage=row.stage,
        plan_hash=row.plan_hash,
        quote_hash=row.quote_hash,
    )


def _quote_hash(
    *,
    operation_id: str,
    estimate_vnd: int,
    contingency_vnd: int,
    category: str,
    rate_card_ids: tuple[str, ...],
    provider_profile_id: str | None,
    provider_profile_revision: int | None,
    cloud_consent_id: str | None,
    stage: str | None,
    plan_hash: str | None,
) -> str:
    payload = {
        "category": category,
        "cloud_consent_id": cloud_consent_id,
        "contingency_vnd": contingency_vnd,
        "estimate_vnd": estimate_vnd,
        "operation_id": operation_id,
        "plan_hash": plan_hash,
        "provider_profile_id": provider_profile_id,
        "provider_profile_revision": provider_profile_revision,
        "rate_card_ids": list(rate_card_ids),
        "stage": stage,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _contingency(estimate_vnd: int) -> int:
    return _ceil_div(estimate_vnd * CONTINGENCY_PERCENT, 100)


def _usd_micros(price_usd_micros_per_million_units: int, measured_units: int) -> int:
    return _ceil_div(price_usd_micros_per_million_units * measured_units, 1_000_000)


def _vnd_from_usd_micros(usd_micros: int) -> int:
    return _ceil_div(usd_micros * FX_RATE_VND_PER_USD, 1_000_000)


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def _authorization_total(row: BudgetAuthorizationRow) -> int:
    return row.estimate_vnd + row.contingency_vnd


def _ledger_by_operation(rows: list[UsageLedger]) -> dict[str, tuple[UsageLedger, ...]]:
    result: dict[str, list[UsageLedger]] = {}
    for row in rows:
        result.setdefault(row.operation_id, []).append(row)
    return {operation_id: tuple(operation_rows) for operation_id, operation_rows in result.items()}


def _committed_operation_total(row: BudgetAuthorizationRow, ledgers: tuple[UsageLedger, ...]) -> int:
    if not ledgers:
        return _authorization_total(row)
    if any(ledger.billing_confidence == "UNKNOWN" or ledger.actual_vnd is None for ledger in ledgers):
        return _authorization_total(row)
    return sum(ledger.actual_vnd or 0 for ledger in ledgers)


def _require_non_negative_int(value: int, field_name: str) -> None:
    if type(value) is not int or value < 0:
        raise BudgetBlocked(f"{field_name.upper()}_INVALID")
