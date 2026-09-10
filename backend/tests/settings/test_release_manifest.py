"""R01: a feature flag may only be switched on when the release manifest records PASS."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.settings import create_settings_router
from app.modules.settings.feature_flags import (
    FLAGS,
    FeatureFlagRejected,
    effective_flags,
    flag_catalog,
    flag_gate_report,
)
from app.modules.settings.release_manifest import (
    DEFAULT_MANIFEST_PATH,
    RELEASE_MANIFEST_ENV,
    ReleaseManifestError,
    load_release_manifest,
)


GATED_FLAGS = tuple(flag.name for flag in flag_catalog() if flag.evidence_feature is not None)


def _write_manifest(tmp_path: Path, features: list[dict[str, object]]) -> Path:
    path = tmp_path / "release-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "releaseScope": "fixture-verified",
                "generatedAt": "2026-09-10T00:00:00Z",
                "commit": "0" * 40,
                "note": "test manifest",
                "features": features,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_missing_manifest_keeps_every_evidence_gated_flag_off(tmp_path: Path) -> None:
    manifest = load_release_manifest(tmp_path / "absent.json")

    assert manifest.available is False
    resolved = effective_flags(manifest=manifest)
    for name in GATED_FLAGS:
        assert resolved[name] is False, name
    # Flags that guard already-shipped reversible UI keep their declared default.
    assert resolved["workspace_tabs"] is True

    with pytest.raises(FeatureFlagRejected) as rejected:
        effective_flags({"cloud_quality_mode": True}, manifest=manifest)
    assert rejected.value.code == "FEATURE_FLAG_EVIDENCE_NOT_PASS:cloud_quality_mode"


@pytest.mark.parametrize("evidence", ["NOT_RUN", "FAIL", "BLOCKED"])
def test_non_pass_evidence_blocks_enabling(tmp_path: Path, evidence: str) -> None:
    path = _write_manifest(
        tmp_path,
        [
            {
                "id": "cloud_quality_live",
                "flag": "cloud_quality_mode",
                "evidence": evidence,
                "summary": "cloud quality has no live evidence",
            }
        ],
    )
    manifest = load_release_manifest(path)

    assert manifest.flag_evidence("cloud_quality_mode") == evidence
    assert manifest.flag_allowed("cloud_quality_mode") is False
    assert effective_flags(manifest=manifest)["cloud_quality_mode"] is False
    with pytest.raises(FeatureFlagRejected) as rejected:
        effective_flags({"cloud_quality_mode": True}, manifest=manifest)
    assert rejected.value.code == "FEATURE_FLAG_EVIDENCE_NOT_PASS:cloud_quality_mode"


def test_pass_evidence_is_the_only_way_to_enable_a_gated_flag(tmp_path: Path) -> None:
    path = _write_manifest(
        tmp_path,
        [
            {
                "id": "cloud_quality_live",
                "flag": "cloud_quality_mode",
                "evidence": "PASS",
                "summary": "authorized cloud smoke with ledger and output",
                "evidenceRef": "docs/validation/quality.md#v039",
            }
        ],
    )
    manifest = load_release_manifest(path)

    assert manifest.flag_allowed("cloud_quality_mode") is True
    assert effective_flags({"cloud_quality_mode": True}, manifest=manifest)["cloud_quality_mode"] is True
    # A feature id that nobody recorded stays un-enable-able even next to a PASS one.
    with pytest.raises(FeatureFlagRejected):
        effective_flags({"vector_index": True}, manifest=manifest)


def test_unsafe_flags_stay_rejected_regardless_of_evidence(tmp_path: Path) -> None:
    path = _write_manifest(
        tmp_path,
        [
            {
                "id": "vector_index_live",
                "flag": "vector_index",
                "evidence": "PASS",
                "summary": "pretend retrieval benchmark",
            },
            {
                "id": "public_export_bypass_live",
                "flag": "public_export_bypass",
                "evidence": "PASS",
                "summary": "pretend rights bypass",
            },
        ],
    )
    manifest = load_release_manifest(path)

    with pytest.raises(FeatureFlagRejected) as vector:
        effective_flags({"vector_index": True}, manifest=manifest)
    assert vector.value.code == "FEATURE_FLAG_UNSAFE:vector_index"
    with pytest.raises(FeatureFlagRejected) as bypass:
        effective_flags({"public_export_bypass": True}, manifest=manifest)
    assert bypass.value.code == "FEATURE_FLAG_UNSAFE:public_export_bypass"


def test_malformed_manifest_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ReleaseManifestError, match="RELEASE_MANIFEST_INVALID"):
        load_release_manifest(path)

    path.write_text(json.dumps({"schemaVersion": 1, "features": []}), encoding="utf-8")
    with pytest.raises(ReleaseManifestError, match="RELEASE_MANIFEST_INVALID:features"):
        load_release_manifest(path)

    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "releaseScope": "fixture-verified",
                "generatedAt": "2026-09-10T00:00:00Z",
                "commit": "0" * 40,
                "features": [{"id": "x", "evidence": "MAYBE", "summary": "?"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ReleaseManifestError, match="RELEASE_MANIFEST_INVALID:evidence"):
        load_release_manifest(path)


def test_environment_override_selects_the_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = _write_manifest(
        tmp_path,
        [{"id": "cloud_quality_live", "flag": "cloud_quality_mode", "evidence": "PASS", "summary": "ok"}],
    )
    monkeypatch.setenv(RELEASE_MANIFEST_ENV, str(path))

    manifest = load_release_manifest()
    assert manifest.path == path
    assert manifest.available is True


def test_repository_manifest_never_claims_unverified_live_features(tmp_path: Path) -> None:
    """The checked-in manifest must exist, parse, and keep the live features off."""
    manifest = load_release_manifest(DEFAULT_MANIFEST_PATH)

    assert manifest.available is True, "docs/validation/release-manifest.json is missing"
    assert manifest.commit, "manifest must record the commit it describes"
    assert manifest.release_scope == "fixture-verified"

    # Every flag that declares evidence must be described by the manifest, otherwise
    # the gate silently degrades to "no evidence recorded".
    for flag in flag_catalog():
        if flag.evidence_feature is not None:
            assert manifest.feature(flag.evidence_feature) is not None, flag.name

    # No live cloud/TTS/quality feature may read PASS in the current phase.
    for feature_id in (
        "cloud_quality_live",
        "vector_index_live",
        "auto_approve_translation_live",
        "public_export_bypass_live",
        "vieneu_tts_live",
        "quality_evaluation_corpus",
    ):
        feature = manifest.feature(feature_id)
        assert feature is not None, feature_id
        assert feature.evidence != "PASS", f"{feature_id} must not claim PASS without evidence"

    disabled = effective_flags(manifest=manifest)
    assert disabled["cloud_quality_mode"] is False
    assert disabled["vector_index"] is False
    assert disabled["auto_approve_translation"] is False
    assert disabled["public_export_bypass"] is False


def test_settings_api_exposes_evidence_and_blocks_gated_flags(tmp_path: Path) -> None:
    path = _write_manifest(
        tmp_path,
        [
            {
                "id": "cloud_quality_live",
                "flag": "cloud_quality_mode",
                "evidence": "NOT_RUN",
                "summary": "no provider profile / consent / budget",
                "evidenceRef": "docs/validation/quality.md#2",
            }
        ],
    )
    import os

    os.environ[RELEASE_MANIFEST_ENV] = str(path)
    try:
        app = FastAPI()
        app.include_router(create_settings_router())
        with TestClient(app) as client:
            listed = client.get("/api/settings/feature-flags")
            assert listed.status_code == 200
            body = listed.json()
            assert body["releaseManifest"]["available"] is True
            flags = {flag["name"]: flag for flag in body["flags"]}
            cloud = flags["cloud_quality_mode"]
            assert cloud["evidence"] == "NOT_RUN"
            assert cloud["enabled"] is False
            assert cloud["disabledReason"] == "FEATURE_FLAG_EVIDENCE_NOT_PASS:cloud_quality_mode"
            assert cloud["evidenceRef"] == "docs/validation/quality.md#2"
            assert flags["workspace_tabs"]["disabledReason"] is None

            blocked = client.put("/api/settings/feature-flags", json={"flags": {"cloud_quality_mode": True}})
            assert blocked.status_code == 400
            assert blocked.json()["detail"] == "FEATURE_FLAG_EVIDENCE_NOT_PASS:cloud_quality_mode"

            unsafe = client.put("/api/settings/feature-flags", json={"flags": {"vector_index": True}})
            assert unsafe.status_code == 400
            assert unsafe.json()["detail"] == "FEATURE_FLAG_UNSAFE:vector_index"

            unknown = client.put("/api/settings/feature-flags", json={"flags": {"nope": True}})
            assert unknown.status_code == 400
            assert unknown.json()["detail"] == "FEATURE_FLAG_UNKNOWN:nope"

            allowed = client.put("/api/settings/feature-flags", json={"flags": {"workspace_tabs": False}})
            assert allowed.status_code == 200
            assert allowed.json()["flags"]["workspace_tabs"] is False
    finally:
        os.environ.pop(RELEASE_MANIFEST_ENV, None)


def test_flag_gate_report_marks_every_flag(tmp_path: Path) -> None:
    manifest = load_release_manifest(tmp_path / "absent.json")
    report = flag_gate_report(manifest)

    assert {entry["name"] for entry in report} == set(FLAGS)
    for entry in report:
        if entry["evidenceFeature"] is None:
            assert entry["disabledReason"] is None
        else:
            assert entry["evidence"] in {"MISSING", "NOT_RUN", "FAIL", "BLOCKED"}
            assert str(entry["disabledReason"]).endswith(str(entry["name"]))
