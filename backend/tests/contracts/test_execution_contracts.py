from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.modules.execution.contracts import (
    AttemptResult,
    CapabilityState,
    ContextSnapshot,
    ExecutionPlan,
    ModelSnapshot,
    PromptEnvelope,
    PromptSegment,
    ProviderError,
    Stage,
    UsageConfidence,
    UsageReport,
    canonical_snapshot_hash,
    parse_snapshot,
)


def _model_snapshot(**overrides: object) -> ModelSnapshot:
    values: dict[str, object] = {
        "provider_id": "gemini",
        "model_id": "gemini-2.5-flash",
        "api_kind": "chat",
        "context_tokens": 100_000,
        "max_output_tokens": 8_192,
        "languages": ["zh-CN", "vi-VN"],
        "capabilities": {
            "stream": CapabilityState.SUPPORTED,
            "structured": CapabilityState.SUPPORTED,
            "translation": CapabilityState.SUPPORTED,
        },
        "availability": "available",
        "pricing": {
            "class": "paid",
            "currency": "USD",
            "input_per_million": Decimal("0.3"),
            "output_per_million": Decimal("2.5"),
        },
        "source_url": "https://example.invalid/model",
        "fetched_at": datetime(2026, 9, 8, tzinfo=timezone.utc),
        "expires_at": datetime(2026, 9, 9, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return ModelSnapshot.model_validate(values)


def test_snapshot_hash_is_stable_across_identity_and_timestamp() -> None:
    first = _model_snapshot(id="018f0000-0000-7000-8000-000000000001")
    second = _model_snapshot(
        id="018f0000-0000-7000-8000-000000000002",
        created_at=datetime(2026, 9, 8, 1, tzinfo=timezone.utc),
    )

    assert canonical_snapshot_hash(first) == canonical_snapshot_hash(second)
    assert len(canonical_snapshot_hash(first)) == 64


def test_parse_snapshot_rejects_unknown_schema_and_tampered_hash() -> None:
    snapshot = _model_snapshot()
    payload = snapshot.model_dump(by_alias=True, mode="json")
    payload["schemaVersion"] = 99
    with pytest.raises(ValueError, match="UNSUPPORTED_SCHEMA"):
        parse_snapshot(payload, ModelSnapshot)

    valid = _model_snapshot()
    tampered = valid.model_dump(by_alias=True, mode="json")
    tampered["hash"] = "0" * 64
    with pytest.raises(ValueError, match="SNAPSHOT_HASH_MISMATCH"):
        parse_snapshot(tampered, ModelSnapshot)


def test_contracts_validate_required_fields_and_nested_types() -> None:
    envelope = PromptEnvelope(
        builder_version="prompt-v1",
        source_language="zh-CN",
        target_language="vi-VN",
        style_revision_id="style-1",
        expected_segment_ids=["segment-1"],
        segments=[PromptSegment(id="segment-1", text="你好")],
        system_policy="translate",
        user_instruction="Giữ tên riêng",
        context_snapshot_id="context-1",
        estimated_input_tokens=12,
        reserved_output_tokens=32,
    )
    context = ContextSnapshot(
        project_id="project-1",
        chapter_id="chapter-1",
        source_revision_id="source-1",
        glossary_revision_ids=[],
        memory_revision_ids=[],
        character_revision_ids=[],
        tm_revision_ids=[],
        selected=[],
        excluded=[],
        token_budget=128,
    )
    plan = ExecutionPlan(
        project_id="project-1",
        chapter_id="chapter-1",
        source_revision_id="source-1",
        stage=Stage.TRANSLATE,
        model_snapshot_id="model-1",
        prompt_envelope_id=envelope.id,
        context_snapshot_id=context.id,
        input_revision_ids=["source-1"],
        idempotency_key="job-1",
    )
    result = AttemptResult(
        attempt_id="attempt-1",
        plan_id=plan.id,
        requested_model="gemini-2.5-flash",
        actual_model=None,
        status="succeeded",
        usage=UsageReport(
            input_tokens=12,
            output_tokens=24,
            cost=Decimal("0.01"),
            currency="USD",
            confidence=UsageConfidence.ESTIMATED,
        ),
    )

    assert plan.schema_version == 1
    assert result.usage.confidence is UsageConfidence.ESTIMATED

    with pytest.raises(ValidationError):
        ProviderError(
            code="BAD",
            message="bad",
            retryable=True,
            billing_state="invalid-state",
            correlation_id="corr-1",
        )
