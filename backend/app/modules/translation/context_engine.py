"""Deterministic translation context selection (task C05).

Selection is a pure function of the input snapshot: items sorted by priority
then stable id, taken greedily until the token budget is exhausted. Items that
do not fit are reported in ``excluded`` with their reason. The engine never
touches or truncates source text (source is handled by the caller outside the
context budget) and never promotes unapproved/candidate items — the caller
only supplies APPROVED memory. Identical snapshots always produce identical
selection, ordering and ``sha256``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math


@dataclass(frozen=True)
class ContextItem:
    id: str
    kind: str
    text: str
    priority: int = 100
    estimated_tokens: int | None = None


@dataclass(frozen=True)
class ContextSelection:
    selected: tuple[ContextItem, ...]
    excluded: tuple[tuple[str, str], ...]  # (item id, reason)
    total_tokens: int
    sha256: str


def estimate_tokens(text: str) -> int:
    """Deterministic rough token estimate (no model involved)."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def select_context(
    items: tuple[ContextItem, ...],
    *,
    token_budget: int,
) -> ContextSelection:
    if token_budget < 0:
        raise ValueError("TOKEN_BUDGET_INVALID")
    ordered = sorted(items, key=lambda item: (item.priority, item.id))
    selected: list[ContextItem] = []
    excluded: list[tuple[str, str]] = []
    total = 0
    for item in ordered:
        cost = item.estimated_tokens if item.estimated_tokens is not None else estimate_tokens(item.text)
        if cost < 0:
            raise ValueError("TOKEN_ESTIMATE_INVALID")
        if total + cost <= token_budget:
            selected.append(item)
            total += cost
        else:
            excluded.append((item.id, "BUDGET"))
    selection = tuple(selected)
    payload = {
        "items": [
            {
                "id": item.id,
                "kind": item.kind,
                "text": item.text,
                "priority": item.priority,
                "estimated_tokens": item.estimated_tokens
                if item.estimated_tokens is not None
                else estimate_tokens(item.text),
            }
            for item in selection
        ],
        "total_tokens": total,
    }
    sha256 = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ContextSelection(selection, tuple(excluded), total, sha256)
