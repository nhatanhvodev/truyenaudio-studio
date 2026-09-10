"""U10: the feature-flag guard must fail closed on unsafe/unknown flags."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.settings import create_settings_router
from app.modules.settings.feature_flags import (
    FeatureFlagRejected,
    effective_flags,
    flag_catalog,
)


def test_defaults_are_explicit_and_unsafe_flags_default_off() -> None:
    resolved = effective_flags()

    assert resolved["workspace_tabs"] is True
    assert resolved["cloud_quality_mode"] is False
    assert resolved["vector_index"] is False
    assert resolved["auto_approve_translation"] is False
    assert resolved["public_export_bypass"] is False
    assert set(resolved) == {flag.name for flag in flag_catalog()}


def test_safe_flags_can_be_toggled_both_ways() -> None:
    # R01 narrowed this: a *safe* flag with an evidence_feature (cloud_quality_mode)
    # can no longer be switched on by the API alone - see
    # tests/settings/test_release_manifest.py. Flags without an evidence requirement
    # still toggle both ways.
    resolved = effective_flags({"workspace_tabs": False, "nested_project_settings": False})

    assert resolved["workspace_tabs"] is False
    assert resolved["nested_project_settings"] is False
    assert effective_flags({"workspace_tabs": True})["workspace_tabs"] is True


def test_unsafe_flags_are_rejected_even_when_false_comparisons_enable_them() -> None:
    with pytest.raises(FeatureFlagRejected) as unsafe:
        effective_flags({"vector_index": True})
    assert unsafe.value.code == "FEATURE_FLAG_UNSAFE:vector_index"

    with pytest.raises(FeatureFlagRejected) as bypass:
        effective_flags({"public_export_bypass": True})
    assert bypass.value.code == "FEATURE_FLAG_UNSAFE:public_export_bypass"

    # Requesting an unsafe flag as False is allowed (it stays off).
    assert effective_flags({"vector_index": False})["vector_index"] is False

    # A truthy non-boolean never turns a flag on.
    assert effective_flags({"workspace_tabs": 1})["workspace_tabs"] is False


def test_unknown_flags_are_rejected_instead_of_silently_ignored() -> None:
    with pytest.raises(FeatureFlagRejected) as unknown:
        effective_flags({"worspace_tabs": True})

    assert unknown.value.code == "FEATURE_FLAG_UNKNOWN:worspace_tabs"


def test_settings_api_lists_flags_and_blocks_unsafe_ones() -> None:
    app = FastAPI()
    app.include_router(create_settings_router())

    with TestClient(app) as client:
        listed = client.get("/api/settings/feature-flags")
        assert listed.status_code == 200
        flags = listed.json()["flags"]
        assert {flag["name"] for flag in flags} == {flag.name for flag in flag_catalog()}
        unsafe = next(flag for flag in flags if flag["name"] == "vector_index")
        assert unsafe["safe"] is False
        assert unsafe["enabled"] is False

        accepted = client.put("/api/settings/feature-flags", json={"flags": {"workspace_tabs": False}})
        assert accepted.status_code == 200
        body = accepted.json()
        assert body["flags"]["workspace_tabs"] is False
        assert body["applied"] is False  # nothing persisted without the rollout task

        blocked = client.put("/api/settings/feature-flags", json={"flags": {"vector_index": True}})
        assert blocked.status_code == 400
        assert blocked.json()["detail"] == "FEATURE_FLAG_UNSAFE:vector_index"

        missing = client.put("/api/settings/feature-flags", json={"flags": {"does_not_exist": True}})
        assert missing.status_code == 400
        assert missing.json()["detail"] == "FEATURE_FLAG_UNKNOWN:does_not_exist"
