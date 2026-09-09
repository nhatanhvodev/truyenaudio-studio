from __future__ import annotations

import pytest

from app.modules.translation.context_engine import ContextItem, estimate_tokens, select_context


def _items() -> tuple[ContextItem, ...]:
    return (
        ContextItem("fact-a", "fact", "林动 là nhân vật chính.", priority=1),
        ContextItem("sum-1", "summary", "Chương 1: 林动 đến thành Thanh Dương.", priority=2),
        ContextItem("sum-2", "summary", "Chương 2: gặp Tiểu Điêu.", priority=2),
    )


def test_selection_respects_budget_and_priority() -> None:
    selection = select_context(_items(), token_budget=10)

    ids = [item.id for item in selection.selected]
    # Tight budget: only the highest-priority fact fits (summaries cost more).
    assert ids == ["fact-a"]
    assert selection.excluded == (("sum-1", "BUDGET"), ("sum-2", "BUDGET"))
    assert selection.total_tokens == estimate_tokens("林动 là nhân vật chính.")


def test_same_snapshot_yields_same_selection_and_hash() -> None:
    first = select_context(_items(), token_budget=400)
    second = select_context(_items(), token_budget=400)

    assert [item.id for item in first.selected] == [item.id for item in second.selected]
    assert first.sha256 == second.sha256
    # Items kept in input order are ordered by (priority, id): sum-1 before sum-2.
    assert [item.id for item in first.selected] == ["fact-a", "sum-1", "sum-2"]


def test_locked_priority_items_are_never_skipped_for_lower_priority() -> None:
    items = (
        ContextItem("low", "summary", "x" * 400, priority=2),
        ContextItem("locked", "fact", "y" * 20, priority=0),
    )

    selection = select_context(items, token_budget=60)

    assert [item.id for item in selection.selected] == ["locked"]
    assert selection.excluded == (("low", "BUDGET"),)


def test_negative_budget_and_estimate_rejected() -> None:
    with pytest.raises(ValueError, match="TOKEN_BUDGET_INVALID"):
        select_context((ContextItem("a", "fact", "text"),), token_budget=-1)
    with pytest.raises(ValueError, match="TOKEN_ESTIMATE_INVALID"):
        select_context(
            (ContextItem("a", "fact", "text", estimated_tokens=-3),),
            token_budget=100,
        )


def test_empty_selection_is_deterministic() -> None:
    selection = select_context((ContextItem("a", "fact", "hello"),), token_budget=1)
    assert selection.selected == ()
    assert selection.excluded == (("a", "BUDGET"),)
    assert selection.total_tokens == 0
    assert len(selection.sha256) == 64
