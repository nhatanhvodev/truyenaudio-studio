"""X07 acceptance: every Phase 2 extension is gated by its *own* flag, and none is on.

The Phase 2 adapters (X01-X05: Groq, NVIDIA NIM, Cerebras, Cloudflare Workers AI,
Hugging Face) are contract-verified offline only - there is no account, no
credential, no terms review and no live invocation for any of them. These tests
pin that state and prove the gate is per extension rather than per group:

* the checked-in manifest records every provider feature as NOT_RUN, so an enable
  request fails with FEATURE_FLAG_EVIDENCE_NOT_PASS:<flag> (the API returns 400);
* a manifest in which exactly one provider reads PASS enables *only* that
  provider - the other four stay blocked, so one passing adapter can never turn
  the group on (plan X07 acceptance criterion);
* the Phase 2 adapters are absent from the curated catalog/registry, so a
  registry cannot even resolve a snapshot for them;
* a capability that is UNKNOWN never reaches an adapter factory (fail-closed);
* local LLM (Ollama / LM Studio / llama.cpp) stays deferred: no flag, no manifest
  feature and no runtime source file refers to one;
* every evidenceRef in the manifest points at a file - and an anchor - that
  really exists in this tree.

Nothing in this module calls a provider, opens a socket or downloads a model: it
only reads the evidence record and the flag guard.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.settings import create_settings_router
from app.modules.execution.contracts import (
    ApiKind,
    Availability,
    CapabilityState,
    ModelSnapshot,
    Pricing,
    PricingClass,
    ProviderCapabilities,
)
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
    load_release_manifest,
)
from app.providers import huggingface
from app.providers.catalog import ProviderDescriptor, curated_provider_catalog
from app.providers.registry import ProviderRegistry, RegistryAuthorization, RegistryError


#: repository root, derived from the checked-in manifest path (never from cwd).
REPO_ROOT = DEFAULT_MANIFEST_PATH.parents[2]

#: (provider id, adapter module stem, manifest feature id == flag name) for X01-X05.
PHASE2_PROVIDERS: tuple[tuple[str, str, str], ...] = (
    ("groq", "groq", "provider_groq_live"),
    ("nvidia-nim", "nim", "provider_nim_live"),
    ("cerebras", "cerebras", "provider_cerebras_live"),
    ("cloudflare-workers-ai", "cloudflare", "provider_cloudflare_live"),
    ("huggingface", "huggingface", "provider_huggingface_live"),
)
PHASE2_FLAGS: tuple[str, ...] = tuple(flag_name for _, _, flag_name in PHASE2_PROVIDERS)
PHASE2_FEATURE_REF_PREFIX = "docs/validation/phase2.md#"

#: Local model runtimes may not be reachable through any Phase 2 extension.
LOCAL_LLM_PATTERN = re.compile(r"ollama|lm[ _-]?studio|llama\.cpp|vllm", re.IGNORECASE)


def _write_manifest(tmp_path: Path, features: list[dict[str, object]]) -> Path:
    path = tmp_path / "release-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "releaseScope": "fixture-verified",
                "generatedAt": "2026-09-11T00:00:00Z",
                "commit": "0" * 40,
                "note": "X07 per-extension gate fixture",
                "features": features,
            }
        ),
        encoding="utf-8",
    )
    return path


def _provider_feature(flag_name: str, evidence: str) -> dict[str, object]:
    return {
        "id": flag_name,
        "evidence": evidence,
        "summary": f"X07 fixture: {flag_name} evidence={evidence}",
        "evidenceRef": f"{PHASE2_FEATURE_REF_PREFIX}6-gate-bật-từng-extension-fail-closed-theo-từng-flag",
        "flag": flag_name,
    }


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_settings_router())
    return TestClient(app)


def _github_anchor(heading: str) -> str:
    """GitHub's heading slug: lowercase, drop punctuation, spaces become hyphens."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s", "-", text)


def _markdown_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            anchors.add(_github_anchor(line.lstrip("#").strip()))
    return anchors


def _snapshot(
    provider_id: str,
    model_id: str,
    *,
    translation: CapabilityState,
) -> ModelSnapshot:
    now = datetime.now(UTC)
    return ModelSnapshot(
        id=f"{provider_id}-snapshot",
        provider_id=provider_id,
        model_id=model_id,
        region=None,
        api_kind=ApiKind.CHAT,
        context_tokens=8_192,
        languages=["zh-CN", "vi-VN"],
        capabilities=ProviderCapabilities(
            stream=CapabilityState.UNKNOWN,
            structured=CapabilityState.UNKNOWN,
            translation=translation,
        ),
        availability=Availability.AVAILABLE,
        pricing=Pricing(pricing_class=PricingClass.UNKNOWN, currency=None),
        source_url="https://example.test/catalog",
        fetched_at=now,
        expires_at=now + timedelta(hours=1),
        benchmark_ref=None,
    )


# --- 1. the evidence record itself ------------------------------------------------


def test_repository_manifest_records_every_phase2_provider_as_not_run() -> None:
    manifest = load_release_manifest(DEFAULT_MANIFEST_PATH)

    assert manifest.available is True
    for provider_id, _stem, flag_name in PHASE2_PROVIDERS:
        feature = manifest.feature(flag_name)
        assert feature is not None, f"{provider_id} has no manifest feature"
        assert feature.evidence == "NOT_RUN", f"{provider_id} must not claim PASS"
        assert feature.flag == flag_name
        assert feature.evidence_ref is not None
        assert feature.evidence_ref.startswith(PHASE2_FEATURE_REF_PREFIX), provider_id
        assert manifest.flag_allowed(flag_name) is False
        assert manifest.flag_evidence(flag_name) == "NOT_RUN"


def test_every_manifest_evidence_ref_points_at_an_existing_file_and_anchor() -> None:
    manifest = load_release_manifest(DEFAULT_MANIFEST_PATH)
    anchors_cache: dict[Path, set[str]] = {}

    for feature in manifest.features:
        ref = feature.evidence_ref
        assert ref, f"{feature.id} has no evidenceRef"
        path_part, _, anchor = ref.partition("#")
        target = REPO_ROOT / path_part
        assert target.is_file(), f"{feature.id}: evidenceRef file is missing: {ref}"
        if not anchor:
            continue
        anchors = anchors_cache.setdefault(target, _markdown_anchors(target))
        assert anchor in anchors, f"{feature.id}: evidenceRef anchor does not exist: {ref}"


# --- 2. the gate is per extension, not per group ----------------------------------


def test_every_phase2_provider_flag_is_registered_and_defaults_off() -> None:
    evidence_features = [FLAGS[name].evidence_feature for name in PHASE2_FLAGS]

    # One flag, one feature - never a shared "phase 2" switch.
    assert len(set(evidence_features)) == len(PHASE2_FLAGS)
    assert set(evidence_features) == set(PHASE2_FLAGS)
    for flag_name in PHASE2_FLAGS:
        flag = FLAGS[flag_name]
        assert flag.safe is True, flag_name
        assert flag.default is False, flag_name
        assert flag.evidence_feature == flag_name


def test_no_phase2_provider_flag_can_be_enabled_from_the_repository_manifest() -> None:
    manifest = load_release_manifest(DEFAULT_MANIFEST_PATH)
    resolved = effective_flags(manifest=manifest)

    for flag_name in PHASE2_FLAGS:
        assert resolved[flag_name] is False, flag_name
        with pytest.raises(FeatureFlagRejected) as rejected:
            effective_flags({flag_name: True}, manifest=manifest)
        assert rejected.value.code == f"FEATURE_FLAG_EVIDENCE_NOT_PASS:{flag_name}"

    # Asking for the whole group at once is refused too - there is no group switch.
    with pytest.raises(FeatureFlagRejected) as group:
        effective_flags({flag_name: True for flag_name in PHASE2_FLAGS}, manifest=manifest)
    assert group.value.code.startswith("FEATURE_FLAG_EVIDENCE_NOT_PASS:provider_")


def test_missing_manifest_keeps_every_phase2_provider_off(tmp_path: Path) -> None:
    manifest = load_release_manifest(tmp_path / "absent.json")

    assert manifest.available is False
    for flag_name in PHASE2_FLAGS:
        assert effective_flags(manifest=manifest)[flag_name] is False
        with pytest.raises(FeatureFlagRejected) as rejected:
            effective_flags({flag_name: True}, manifest=manifest)
        assert rejected.value.code == f"FEATURE_FLAG_EVIDENCE_NOT_PASS:{flag_name}"


def test_one_provider_with_pass_evidence_never_enables_the_others(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verified = PHASE2_FLAGS[0]
    path = _write_manifest(
        tmp_path,
        [
            _provider_feature(flag_name, "PASS" if flag_name == verified else "NOT_RUN")
            for flag_name in PHASE2_FLAGS
        ],
    )
    manifest = load_release_manifest(path)

    # Only the provider that actually earned PASS can be switched on.
    assert effective_flags({verified: True}, manifest=manifest)[verified] is True
    for flag_name in PHASE2_FLAGS[1:]:
        assert effective_flags(manifest=manifest)[flag_name] is False, flag_name
        with pytest.raises(FeatureFlagRejected) as rejected:
            effective_flags({flag_name: True}, manifest=manifest)
        assert rejected.value.code == f"FEATURE_FLAG_EVIDENCE_NOT_PASS:{flag_name}"

    # The same over the real API: 200 for the verified provider, 400 for the rest.
    monkeypatch.setenv(RELEASE_MANIFEST_ENV, str(path))
    with _client() as client:
        allowed = client.put("/api/settings/feature-flags", json={"flags": {verified: True}})
        assert allowed.status_code == 200
        assert allowed.json()["flags"][verified] is True
        for flag_name in PHASE2_FLAGS[1:]:
            blocked = client.put("/api/settings/feature-flags", json={"flags": {flag_name: True}})
            assert blocked.status_code == 400, flag_name
            assert blocked.json()["detail"] == f"FEATURE_FLAG_EVIDENCE_NOT_PASS:{flag_name}"


def test_settings_api_returns_400_for_every_phase2_provider_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RELEASE_MANIFEST_ENV, str(DEFAULT_MANIFEST_PATH))

    with _client() as client:
        listed = client.get("/api/settings/feature-flags")
        assert listed.status_code == 200
        flags = {entry["name"]: entry for entry in listed.json()["flags"]}

        for flag_name in PHASE2_FLAGS:
            entry = flags[flag_name]
            assert entry["safe"] is True
            assert entry["enabled"] is False
            assert entry["evidence"] == "NOT_RUN"
            assert entry["evidenceFeature"] == flag_name
            assert entry["disabledReason"] == f"FEATURE_FLAG_EVIDENCE_NOT_PASS:{flag_name}"
            assert str(entry["evidenceRef"]).startswith(PHASE2_FEATURE_REF_PREFIX)

            blocked = client.put("/api/settings/feature-flags", json={"flags": {flag_name: True}})
            assert blocked.status_code == 400, flag_name
            assert blocked.json()["detail"] == f"FEATURE_FLAG_EVIDENCE_NOT_PASS:{flag_name}"


def test_flag_gate_report_marks_every_phase2_provider_as_blocked() -> None:
    manifest = load_release_manifest(DEFAULT_MANIFEST_PATH)
    report = {entry["name"]: entry for entry in flag_gate_report(manifest)}

    for flag_name in PHASE2_FLAGS:
        entry = report[flag_name]
        assert entry["enabled"] is False
        assert entry["disabledReason"] == f"FEATURE_FLAG_EVIDENCE_NOT_PASS:{flag_name}"
        assert entry["evidence"] == "NOT_RUN"


# --- 3. the extensions are not reachable from the provider registry ---------------


def test_phase2_adapters_are_not_registered_in_the_curated_catalog() -> None:
    catalog = curated_provider_catalog()
    registered = {descriptor.provider_id for descriptor in catalog.descriptors()}

    # The three Phase 1 providers are registered; no Phase 2 extension is. Registering
    # one of them is exactly the step that requires its own X07 evidence, so this
    # assertion is the gate - not an incidental snapshot of the catalog.
    assert registered >= {"gemini", "qwen", "openrouter"}
    assert registered.isdisjoint({provider_id for provider_id, _, _ in PHASE2_PROVIDERS})

    for provider_id, _stem, _flag in PHASE2_PROVIDERS:
        with pytest.raises(KeyError, match="PROVIDER_NOT_FOUND"):
            catalog.get(provider_id)

    # A registry over the curated catalog therefore cannot resolve a Phase 2
    # snapshot even when its authorization is otherwise perfectly valid.
    registry = ProviderRegistry(catalog=catalog, profile_revision_resolver=lambda profile_id: 1)
    for provider_id, _stem, _flag in PHASE2_PROVIDERS:
        with pytest.raises(RegistryError, match="MODEL_UNAVAILABLE"):
            registry.resolve(
                f"{provider_id}-snapshot",
                RegistryAuthorization(profile_id="profile-1", profile_revision=1, model_snapshot_id=f"{provider_id}-snapshot"),
            )


def test_unknown_capability_fails_closed_before_any_factory_runs() -> None:
    dispatched: list[str] = []
    descriptor = ProviderDescriptor(
        "phase2-extension",
        "Phase 2 extension",
        "phase2-adapter",
        models=[_snapshot("phase2-extension", "m1", translation=CapabilityState.UNKNOWN)],
        factory=lambda authorization: dispatched.append("factory") or authorization,
    )
    registry = ProviderRegistry([descriptor], profile_revision_resolver=lambda profile_id: 1)
    authorization = RegistryAuthorization(
        profile_id="profile-1",
        profile_revision=1,
        model_snapshot_id=descriptor.models[0].id,
    )

    with pytest.raises(RegistryError, match="CAPABILITY_UNKNOWN"):
        registry.resolve(descriptor.models[0].id, authorization)
    assert dispatched == [], "an UNKNOWN capability must never reach an adapter factory"

    # The same descriptor with an explicit SUPPORTED capability does dispatch, so
    # the failure above is the capability state, not an unrelated guard.
    supported = ProviderDescriptor(
        "phase2-extension",
        "Phase 2 extension",
        "phase2-adapter",
        models=[_snapshot("phase2-extension", "m1", translation=CapabilityState.SUPPORTED)],
        factory=lambda authorization: dispatched.append("factory") or authorization,
    )
    ProviderRegistry([supported], profile_revision_resolver=lambda profile_id: 1).resolve(
        supported.models[0].id,
        authorization,
    )
    assert dispatched == ["factory"]


# --- 4. local LLM stays deferred ---------------------------------------------------


def test_local_llm_stays_deferred() -> None:
    manifest = load_release_manifest(DEFAULT_MANIFEST_PATH)

    for flag in flag_catalog():
        haystack = f"{flag.name} {flag.description} {flag.evidence_feature or ''}"
        assert not LOCAL_LLM_PATTERN.search(haystack), flag.name

    for feature in manifest.features:
        haystack = f"{feature.id} {feature.summary} {feature.flag or ''}"
        assert not LOCAL_LLM_PATTERN.search(haystack), feature.id

    sources = sorted((REPO_ROOT / "backend" / "app" / "providers").glob("*.py"))
    assert sources, "provider sources not found"
    for path in sources:
        assert not LOCAL_LLM_PATTERN.search(path.read_text(encoding="utf-8")), path.name


def test_huggingface_extension_is_hosted_only_and_downloads_nothing() -> None:
    assert huggingface.SOURCE_HOSTED == "HOSTED"
    assert huggingface.SUPPORTED_TASKS == frozenset({huggingface.TASK_TEXT_GENERATION})

    tree = ast.parse(Path(huggingface.__file__).read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    # The docstring *describes* the boundary, so the check reads the import graph
    # (not the text): no local model runtime may be imported by this adapter.
    assert imported.isdisjoint({"transformers", "torch", "huggingface_hub", "sentence_transformers", "llama_cpp"})
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called.isdisjoint({"snapshot_download", "hf_hub_download", "from_pretrained"})
