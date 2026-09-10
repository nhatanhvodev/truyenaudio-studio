"""Feature-flag guard for Phase-1 rollout (task U10 / R01 seam).

Flags are **fail-closed**:

- ``safe`` flags may be enabled by the caller (they only expose UI/flow that is
  already implemented and reversible);
- ``unsafe`` flags are never enabled through the API — requesting one is rejected
  with ``FEATURE_FLAG_UNSAFE:<name>`` so a rollout cannot switch on a behaviour
  that still needs its own acceptance (plan §R01/§2);
- unknown names are rejected (``FEATURE_FLAG_UNKNOWN:<name>``) instead of being
  silently stored, which would let a typo look like a disabled feature.

The guard returns the *effective* flag set (defaults merged with the accepted
overrides); persistence of the choice belongs to the rollout task, so this module
never writes to the database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureFlag:
    name: str
    safe: bool
    default: bool
    description: str


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
        ),
        FeatureFlag(
            "vector_index",
            False,
            False,
            "Vector index tuỳ chọn (X06) — chưa nghiệm thu, không được bật qua API.",
        ),
        FeatureFlag(
            "auto_approve_translation",
            False,
            False,
            "Tự động duyệt bản dịch — bỏ qua bước review, chưa nghiệm thu.",
        ),
        FeatureFlag(
            "public_export_bypass",
            False,
            False,
            "Bỏ qua cổng quyền khi export công khai — vi phạm F03 nếu bật.",
        ),
    )
}


def flag_catalog() -> tuple[FeatureFlag, ...]:
    return tuple(FLAGS.values())


def effective_flags(requested: Mapping[str, object] | None = None) -> dict[str, bool]:
    """Defaults merged with accepted overrides; raises on unsafe/unknown flags."""
    resolved = {flag.name: flag.default for flag in FLAGS.values()}
    if not requested:
        return resolved
    for name, value in requested.items():
        flag = FLAGS.get(name)
        if flag is None:
            raise FeatureFlagRejected(f"FEATURE_FLAG_UNKNOWN:{name}")
        enabled = value is True
        if enabled and not flag.safe:
            raise FeatureFlagRejected(f"FEATURE_FLAG_UNSAFE:{name}")
        resolved[name] = enabled
    return resolved
