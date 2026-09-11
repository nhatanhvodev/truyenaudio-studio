"""Deterministic translation context selection (task C05).

Selection is a pure function of the input snapshot: items sorted by priority
then stable id, taken greedily until the token budget is exhausted. Items that
do not fit are reported in ``excluded`` with their reason. The engine never
touches or truncates source text (source is handled by the caller outside the
context budget) and never promotes unapproved/candidate items — the caller
only supplies APPROVED memory. Identical snapshots always produce identical
selection, ordering and ``sha256``.

Task X06 adds an **optional decoration** on top of that selector: a caller that
already holds a validated user-supplied vector index may pass a
:class:`VectorContextHint` (top-K reference ids plus the reason retrieval did or
did not run). The hint never changes what is selected - the structured rules
above stay the source of truth - and it defaults to `None`, so an off-path
caller (no index imported, index disabled) gets exactly the pre-X06 result,
including the same `sha256`.
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
class VectorContextHint:
    """What the optional vector layer contributed to a selection (task X06).

    ``used`` is False whenever retrieval fell back (no index, disabled, stale,
    missing or mismatched query embedding); ``reason`` then carries the
    machine-readable cause so a trace can explain the fallback instead of
    implying the vector path ran.
    """

    used: bool
    reason: str
    selected: tuple[str, ...] = ()

    def trace(self) -> dict[str, object]:
        return {"used": self.used, "reason": self.reason, "selected": list(self.selected)}


@dataclass(frozen=True)
class ContextSelection:
    selected: tuple[ContextItem, ...]
    excluded: tuple[tuple[str, str], ...]  # (item id, reason)
    total_tokens: int
    sha256: str
    #: None for an off-path selection: nothing about the result changes.
    vector: VectorContextHint | None = None


def estimate_tokens(text: str) -> int:
    """Deterministic rough token estimate (no model involved)."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def select_context(
    items: tuple[ContextItem, ...],
    *,
    token_budget: int,
    vector: VectorContextHint | None = None,
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
    if vector is not None and vector.used:
        # Recorded only when the vector layer actually contributed. A fallback
        # therefore leaves the hashed payload - and the selection itself - byte
        # for byte identical to the pre-X06 result; its reason still travels on
        # the returned hint and in the caller's trace. The hint never adds, drops
        # or reorders an item.
        payload["vector"] = vector.trace()
    sha256 = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ContextSelection(selection, tuple(excluded), total, sha256, vector)
