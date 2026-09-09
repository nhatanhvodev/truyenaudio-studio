from __future__ import annotations

import pytest

from app.contracts import JobKind
from app.modules.jobs.handlers import PLAN_REQUIRED_KINDS, PlanRequiredError, require_plan


def test_cloud_kinds_require_a_complete_immutable_plan() -> None:
    assert JobKind.TRANSLATE in PLAN_REQUIRED_KINDS
    assert JobKind.SYNTHESIZE in PLAN_REQUIRED_KINDS
    assert JobKind.IMPORT not in PLAN_REQUIRED_KINDS

    with pytest.raises(PlanRequiredError):
        require_plan(JobKind.TRANSLATE, None)

    with pytest.raises(PlanRequiredError):
        require_plan(
            JobKind.TRANSLATE,
            {"projectId": "p1", "profileId": "profile-1"},
        )

    plan = require_plan(
        JobKind.TRANSLATE,
        {
            "projectId": "p1",
            "profileId": "profile-1",
            "cloudConsentId": "consent-1",
            "budgetAuthorizationId": "auth-1",
        },
    )
    assert plan["profileId"] == "profile-1"


def test_local_kinds_do_not_require_a_plan() -> None:
    assert require_plan(JobKind.IMPORT, None) == {}


def test_plan_error_code_is_kind_specific() -> None:
    try:
        require_plan(JobKind.EXPORT, None)
        raise AssertionError("expected PlanRequiredError")
    except PlanRequiredError as exc:
        assert exc.code == "EXPORT_PLAN_REQUIRED"
