from __future__ import annotations

import pytest

from datetime import UTC, datetime, timedelta

from app.modules.execution.contracts import (
    ApiKind,
    Availability,
    CapabilityState,
    ModelSnapshot,
    Pricing,
    PricingClass,
    ProviderCapabilities,
)
from app.modules.translation.prompt_builder import PromptBuildInput, PromptBuilder


@pytest.fixture
def model_snapshot() -> ModelSnapshot:
    now = datetime.now(UTC)
    return ModelSnapshot(
        provider_id="p1",
        model_id="m1",
        api_kind=ApiKind.CHAT,
        context_tokens=4096,
        languages=["zh-CN", "vi-VN"],
        capabilities=ProviderCapabilities(
            stream=CapabilityState.SUPPORTED,
            structured=CapabilityState.SUPPORTED,
            translation=CapabilityState.SUPPORTED,
        ),
        availability=Availability.AVAILABLE,
        pricing=Pricing(pricing_class=PricingClass.PAID, currency="USD"),
        source_url="https://example.test/model",
        fetched_at=now,
        expires_at=now + timedelta(hours=1),
    )


def test_prompt_builder_creates_versioned_envelope_and_preserves_segment_order(model_snapshot: ModelSnapshot) -> None:
    envelope = PromptBuilder().build(
        PromptBuildInput(
            source_language="zh-CN",
            target_language="vi-VN",
            style_revision_id="style-2",
            context_snapshot_id="context-3",
            segments=(("seg-2", "第二段"), ("seg-1", "第一段")),
            glossary=(('门', 'cửa'),),
            user_instruction="Giữ nguyên số liệu.",
        ),
        model_snapshot,
    )

    assert envelope.schema_version == 1
    assert envelope.builder_version == "prompt-builder.v1"
    assert envelope.expected_segment_ids == ["seg-2", "seg-1"]
    assert [segment.id for segment in envelope.segments] == ["seg-2", "seg-1"]
    assert "门" in envelope.user_instruction
    assert "cửa" in envelope.user_instruction
    assert "第二段" in envelope.user_instruction


def test_prompt_builder_rejects_empty_segments_and_invalid_model_budget(model_snapshot: ModelSnapshot) -> None:
    with pytest.raises(ValueError, match="SEGMENTS_REQUIRED"):
        PromptBuilder().build(
            PromptBuildInput(
                source_language="zh-CN",
                target_language="vi-VN",
                style_revision_id="style-1",
                context_snapshot_id="context-1",
                segments=(),
            ),
            model_snapshot,
        )

    with pytest.raises(ValueError, match="SEGMENT_IDS_NOT_UNIQUE"):
        PromptBuilder().build(
            PromptBuildInput(
                source_language="zh-CN",
                target_language="vi-VN",
                style_revision_id="style-1",
                context_snapshot_id="context-1",
                segments=(("same", "一"), ("same", "二")),
            ),
            model_snapshot,
        )
