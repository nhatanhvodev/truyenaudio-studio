from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.contracts import Usage, UsageUnit
from app.db.models import BudgetAuthorization, RateCard, UsageLedger
from app.modules.budgets.guard import BudgetBlocked, BudgetGuard


NOW = datetime(2026, 8, 19, 8, 0, 0, tzinfo=UTC)


@pytest.fixture
def guard(db_session) -> BudgetGuard:
    return BudgetGuard(db_session, now=lambda: NOW)


def test_regular_job_cannot_spend_reserved_budget(db_session, guard: BudgetGuard) -> None:
    _seed_confirmed_usage(db_session, 399_000)

    quote = guard.quote("translate-chapter-001", 1_000, "REGULAR")

    with pytest.raises(BudgetBlocked) as exc:
        guard.authorize(quote)
    assert exc.value.reason == "BUDGET_REGULAR_CAP_EXCEEDED"


def test_total_never_exceeds_hard_limit(db_session, guard: BudgetGuard) -> None:
    _seed_confirmed_usage(db_session, 499_000)

    quote = guard.quote("repair-chapter-001", 1_000, "QA_REPAIR")

    with pytest.raises(BudgetBlocked) as exc:
        guard.authorize(quote)
    assert exc.value.reason == "BUDGET_HARD_LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    ("category", "expected_reason"),
    [
        ("QA_REPAIR", "BUDGET_QA_REPAIR_RESERVE_EXCEEDED"),
        ("RERENDER", "BUDGET_RERENDER_RESERVE_EXCEEDED"),
    ],
)
def test_reserve_jobs_cannot_spend_past_their_bucket(
    guard: BudgetGuard,
    category: str,
    expected_reason: str,
) -> None:
    quote = guard.quote(f"{category.lower()}-too-large", 43_479, category)

    with pytest.raises(BudgetBlocked) as exc:
        guard.authorize(quote)

    assert exc.value.reason == expected_reason


def test_qa_reserve_counts_existing_held_budget(guard: BudgetGuard) -> None:
    guard.authorize(guard.quote("qa-held", 43_000, "QA_REPAIR"))

    with pytest.raises(BudgetBlocked) as exc:
        guard.authorize(guard.quote("qa-next", 500, "QA_REPAIR"))

    assert exc.value.reason == "BUDGET_QA_REPAIR_RESERVE_EXCEEDED"


def test_qa_reserve_counts_committed_billing_unknown_budget(db_session, guard: BudgetGuard) -> None:
    authorization = guard.authorize(guard.quote("qa-unknown", 43_000, "QA_REPAIR"))
    stored = db_session.get(BudgetAuthorization, authorization.id)
    stored.status = "COMMITTED"
    db_session.add(
        UsageLedger(
            id="018f0000-0000-7000-8000-00000000b001",
            provider="qwen",
            model="qwen-mt-flash",
            region="frankfurt",
            operation_id="qa-unknown",
            unit=UsageUnit.INPUT_TOKEN.value,
            measured_units=1,
            billing_confidence="UNKNOWN",
        )
    )
    db_session.commit()

    with pytest.raises(BudgetBlocked) as exc:
        guard.authorize(guard.quote("qa-after-unknown", 500, "QA_REPAIR"))

    assert exc.value.reason == "BUDGET_QA_REPAIR_RESERVE_EXCEEDED"


def test_warning_threshold_marks_quote_without_blocking(db_session, guard: BudgetGuard) -> None:
    _seed_confirmed_usage(db_session, 349_000)

    quote = guard.quote("translate-warning", 1_000, "REGULAR")
    authorization = guard.authorize(quote)

    assert quote.warnings == ("BUDGET_WARNING_THRESHOLD_EXCEEDED",)
    assert authorization.id == quote.id


def test_quote_from_usage_requires_exact_current_rate_card(db_session, guard: BudgetGuard) -> None:
    _seed_rate_card(db_session, unit=UsageUnit.OUTPUT_TOKEN.value)

    with pytest.raises(BudgetBlocked) as exc:
        guard.quote_usage(
            operation_id="translate-segment-001",
            provider="qwen",
            model="qwen-mt-flash",
            region="frankfurt",
            usage=(Usage(UsageUnit.INPUT_TOKEN.value, 100),),
            category="REGULAR",
        )

    assert exc.value.reason == "BUDGET_RATE_MISSING"


def test_quote_from_usage_uses_integer_fx_and_contingency(db_session, guard: BudgetGuard) -> None:
    rate = _seed_rate_card(db_session, unit=UsageUnit.INPUT_TOKEN.value, usd_micros_per_million=1_000_000)

    quote = guard.quote_usage(
        operation_id="translate-million-input",
        provider="qwen",
        model="qwen-mt-flash",
        region="frankfurt",
        usage=(Usage(UsageUnit.INPUT_TOKEN.value, 1_000_000),),
        category="REGULAR",
    )

    assert quote.estimate_vnd == 26_500
    assert quote.contingency_vnd == 3_975
    assert quote.rate_card_id == rate.id
    assert quote.expires_at == NOW + timedelta(minutes=15)


def test_commit_usage_moves_hold_to_committed_and_writes_integer_ledger(db_session, guard: BudgetGuard) -> None:
    rate = _seed_rate_card(db_session, unit=UsageUnit.INPUT_TOKEN.value, usd_micros_per_million=1_000_000)
    quote = guard.quote_usage(
        operation_id="translate-million-input",
        provider="qwen",
        model="qwen-mt-flash",
        region="frankfurt",
        usage=(Usage(UsageUnit.INPUT_TOKEN.value, 1_000_000, provider_request_id="req-001"),),
        category="REGULAR",
    )
    authorization = guard.authorize(quote)

    guard.commit_usage(
        authorization.id,
        provider="qwen",
        model="qwen-mt-flash",
        region="frankfurt",
        usage=(Usage(UsageUnit.INPUT_TOKEN.value, 1_000_000, provider_request_id="req-001"),),
    )

    stored = db_session.get(BudgetAuthorization, authorization.id)
    ledger = db_session.query(UsageLedger).one()
    assert stored.status == "COMMITTED"
    assert ledger.rate_card_id == rate.id
    assert ledger.actual_usd_micros == 1_000_000
    assert ledger.fx_rate == 26_500
    assert ledger.actual_vnd == 26_500
    assert ledger.billing_confidence == "CONFIRMED"


def _seed_confirmed_usage(db_session, actual_vnd: int) -> None:
    db_session.add(
        UsageLedger(
            id=f"018f0000-0000-7000-8000-{actual_vnd:012x}"[-36:],
            provider="qwen",
            model="qwen-mt-flash",
            region="frankfurt",
            operation_id=f"seed-{actual_vnd}",
            unit=UsageUnit.INPUT_TOKEN.value,
            measured_units=1,
            actual_usd_micros=0,
            fx_rate=26_500,
            actual_vnd=actual_vnd,
            billing_confidence="CONFIRMED",
        )
    )
    db_session.commit()


def _seed_rate_card(
    db_session,
    *,
    unit: str,
    usd_micros_per_million: int = 1_000,
) -> RateCard:
    rate = RateCard(
        id=f"018f0000-0000-7000-8000-00000000{len(unit):04x}",
        provider="qwen",
        model="qwen-mt-flash",
        region="frankfurt",
        unit=unit,
        price_usd_micros_per_million_units=usd_micros_per_million,
        effective_from=NOW - timedelta(days=1),
        source_url="https://example.test/qwen-rate-card",
        source_note="fixture",
        verified_at=NOW,
    )
    db_session.add(rate)
    db_session.commit()
    return rate
