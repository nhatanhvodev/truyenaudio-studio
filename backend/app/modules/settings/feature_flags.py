"""Feature-flag guard for Phase-1 rollout (task U10 / R01 seam).

Flags are **fail-closed** on four independent axes:

- ``safe`` flags may be enabled by the caller (they only expose UI/flow that is
  already implemented and reversible);
- ``unsafe`` flags are never enabled through the API - requesting one is rejected
  with ``FEATURE_FLAG_UNSAFE:<name>`` so a rollout cannot switch on a behaviour
  that still needs its own acceptance (plan R01/2);
- unknown names are rejected (``FEATURE_FLAG_UNKNOWN:<name>``) instead of being
  silently stored, which would let a typo look like a disabled feature;
- **evidence** (R01): a flag that declares ``evidence_feature`` can only be
  switched on while ``docs/validation/release-manifest.json`` records PASS for
  that feature. A live feature whose evidence is NOT_RUN/FAIL/BLOCKED - or whose
  manifest entry is missing, or whose manifest file is absent - stays off, and an
  enable request raises ``FEATURE_FLAG_EVIDENCE_NOT_PASS:<name>``.

The guard *reads* the release manifest; it never writes it. Switching a live
cloud/TTS/quality feature on therefore requires an evidence record a human
updated, not a flag API call (plan G-RELEASE / G-LIVE).

The guard returns the *effective* flag set (defaults merged with the accepted
overrides); persistence of the choice belongs to the rollout task, so this module
never writes to the database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.modules.settings.release_manifest import ReleaseManifest, load_release_manifest


@dataclass(frozen=True)
class FeatureFlag:
    name: str
    safe: bool
    default: bool
    description: str
    #: Release-manifest feature id that must read PASS before this flag may be on.
    #: None means the flag only guards already-shipped, reversible UI behaviour.
    evidence_feature: str | None = None


class FeatureFlagRejected(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


FLAGS: dict[str, FeatureFlag] = {
    flag.name: flag
    for flag in (
        FeatureFlag(
            "workspace_tabs",
            True,
            True,
            "Thanh tab/dock của không gian làm việc (U05) — đã có test, có thể tắt để quay về điều hướng cũ.",
        ),
        FeatureFlag(
            "draft_streaming",
            True,
            True,
            "Theo dõi nháp job theo luồng (U07/J04) — chỉ đọc, không thay đổi bản dịch đã duyệt.",
        ),
        FeatureFlag(
            "nested_project_settings",
            True,
            True,
            "Cài đặt lồng theo dự án (U02) — chỉ là route/UI.",
        ),
        FeatureFlag(
            "cloud_quality_mode",
            True,
            False,
            "Chế độ chất lượng cloud (U08) — vẫn cần consent + budget cho từng lần gọi.",
            evidence_feature="cloud_quality_live",
        ),
        FeatureFlag(
            "vector_index",
            False,
            False,
            "Vector index tuỳ chọn (X06) — chưa nghiệm thu, không được bật qua API.",
            evidence_feature="vector_index_live",
        ),
        FeatureFlag(
            "auto_approve_translation",
            False,
            False,
            "Tự động duyệt bản dịch — bỏ qua bước review, chưa nghiệm thu.",
            evidence_feature="auto_approve_translation_live",
        ),
        FeatureFlag(
            "public_export_bypass",
            False,
            False,
            "Bỏ qua cổng quyền khi export công khai — vi phạm F03 nếu bật.",
            evidence_feature="public_export_bypass_live",
        ),
        # Phase 2 extensions (X01–X05). One flag per provider, each gated by its
        # *own* manifest feature: a provider that has no live evidence stays off
        # even when a different provider has already been verified, so one
        # passing adapter can never switch the group on (plan X07 acceptance).
        FeatureFlag(
            "provider_groq_live",
            True,
            False,
            "Gọi Groq thật (X01) — chỉ bật khi manifest ghi PASS cho provider_groq_live.",
            evidence_feature="provider_groq_live",
        ),
        FeatureFlag(
            "provider_nim_live",
            True,
            False,
            "Gọi NVIDIA NIM thật (X02) — account/license là gate riêng của provider này.",
            evidence_feature="provider_nim_live",
        ),
        FeatureFlag(
            "provider_cerebras_live",
            True,
            False,
            "Gọi Cerebras thật (X03) — cần evidence live riêng, không suy từ adapter khác.",
            evidence_feature="provider_cerebras_live",
        ),
        FeatureFlag(
            "provider_cloudflare_live",
            True,
            False,
            "Gọi Cloudflare Workers AI thật (X04) — cần account/region/permission được xác nhận.",
            evidence_feature="provider_cloudflare_live",
        ),
        FeatureFlag(
            "provider_huggingface_live",
            True,
            False,
            "Gọi Hugging Face hosted inference thật (X05) — không bật local LLM/tải model.",
            evidence_feature="provider_huggingface_live",
        ),
    )
}


def flag_catalog() -> tuple[FeatureFlag, ...]:
    return tuple(FLAGS.values())


def active_manifest(manifest: ReleaseManifest | None = None) -> ReleaseManifest:
    return manifest if manifest is not None else load_release_manifest()


def flag_evidence_allows(flag: FeatureFlag, manifest: ReleaseManifest) -> bool:
    """True when the manifest does not gate the flag, or records PASS for it."""
    if flag.evidence_feature is None:
        return True
    return manifest.feature_allows(flag.evidence_feature)


def flag_disabled_reason(flag: FeatureFlag, manifest: ReleaseManifest) -> str | None:
    """Machine code explaining why the flag cannot be switched on, or None."""
    if not flag.safe:
        return f"FEATURE_FLAG_UNSAFE:{flag.name}"
    if not flag_evidence_allows(flag, manifest):
        return f"FEATURE_FLAG_EVIDENCE_NOT_PASS:{flag.name}"
    return None


def effective_flags(
    requested: Mapping[str, object] | None = None,
    *,
    manifest: ReleaseManifest | None = None,
) -> dict[str, bool]:
    """Defaults merged with accepted overrides; raises on unsafe/unknown/gated flags.

    A flag whose evidence is not PASS is forced off even when its declared default
    is True, so a stale manifest cannot leave a live feature switched on.
    """
    active = active_manifest(manifest)
    resolved: dict[str, bool] = {}
    for flag in FLAGS.values():
        resolved[flag.name] = flag.default if flag_evidence_allows(flag, active) else False
    if not requested:
        return resolved
    for name, value in requested.items():
        flag = FLAGS.get(name)
        if flag is None:
            raise FeatureFlagRejected(f"FEATURE_FLAG_UNKNOWN:{name}")
        enabled = value is True
        if enabled and not flag.safe:
            raise FeatureFlagRejected(f"FEATURE_FLAG_UNSAFE:{name}")
        if enabled and not flag_evidence_allows(flag, active):
            raise FeatureFlagRejected(f"FEATURE_FLAG_EVIDENCE_NOT_PASS:{name}")
        resolved[name] = enabled
    return resolved


def flag_gate_report(manifest: ReleaseManifest | None = None) -> list[dict[str, object]]:
    """Per-flag rollout view: effective value, evidence state and blocking code."""
    active = active_manifest(manifest)
    resolved = effective_flags(manifest=active)
    report: list[dict[str, object]] = []
    for flag in FLAGS.values():
        feature = active.feature(flag.evidence_feature) if flag.evidence_feature else None
        report.append(
            {
                "name": flag.name,
                "safe": flag.safe,
                "default": flag.default,
                "enabled": resolved[flag.name],
                "description": flag.description,
                "evidenceFeature": flag.evidence_feature,
                "evidence": active.flag_evidence(flag.name),
                "evidenceRef": feature.evidence_ref if feature is not None else None,
                "disabledReason": flag_disabled_reason(flag, active),
            }
        )
    return report


__all__ = [
    "FLAGS",
    "FeatureFlag",
    "FeatureFlagRejected",
    "active_manifest",
    "effective_flags",
    "flag_catalog",
    "flag_disabled_reason",
    "flag_evidence_allows",
    "flag_gate_report",
]
