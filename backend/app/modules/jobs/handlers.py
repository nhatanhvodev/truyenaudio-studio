"""Immutable execution plan requirement for worker handlers (task J01).

Cloud/model-backed job kinds must be enqueued with an immutable plan payload
(carried on the job row) so the worker handler can re-validate profile,
consent, budget authorization and revision before any provider dispatch.
Kinds in ``PLAN_REQUIRED_KINDS`` are refused at claim time when the plan is
absent; the plan is never mutated after enqueue (idempotent re-enqueue keeps
the first plan).
"""

from __future__ import annotations

from collections.abc import Mapping

from app.contracts import JobKind


PLAN_REQUIRED_KINDS = frozenset(
    {
        JobKind.TRANSLATE,
        JobKind.REVIEW,
        JobKind.REPAIR_TRANSLATION,
        JobKind.PREVIEW_TTS,
        JobKind.SYNTHESIZE,
        JobKind.MASTER,
        JobKind.AUDIO_QA,
        JobKind.EXPORT,
    }
)


class PlanRequiredError(Exception):
    def __init__(self, kind: JobKind) -> None:
        self.code = f"{kind.value}_PLAN_REQUIRED"
        super().__init__(f"{kind.value} jobs require an immutable execution plan")


def require_plan(kind: JobKind, plan: Mapping[str, object] | None) -> Mapping[str, object]:
    """Return the plan for a model-backed kind or fail closed before dispatch."""
    if kind in PLAN_REQUIRED_KINDS:
        if plan is None or not isinstance(plan, Mapping):
            raise PlanRequiredError(kind)
        required = ("projectId", "profileId", "cloudConsentId", "budgetAuthorizationId")
        missing = [key for key in required if plan.get(key) is None]
        if missing:
            raise PlanRequiredError(kind)
    return plan or {}
