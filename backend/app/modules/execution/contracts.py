from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Mapping, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.contracts import new_id


_HEX64 = r"^[0-9a-fA-F]{64}$"


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.capitalize() for part in value.split("_")[1:]),
        populate_by_name=True,
        extra="forbid",
        validate_assignment=True,
    )

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    id: str = Field(default_factory=new_id, min_length=1)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), alias="createdAt"
    )
    hash: str | None = Field(default=None, pattern=_HEX64)


class CapabilityState(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class ApiKind(StrEnum):
    CHAT = "chat"
    NATIVE_MT = "nativeMt"


class Availability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class PricingClass(StrEnum):
    FREE = "free"
    PAID = "paid"
    UNKNOWN = "unknown"


class Stage(StrEnum):
    ANALYZE = "analyze"
    TRANSLATE = "translate"
    REVIEW = "review"
    POLISH = "polish"
    REPAIR = "repair"
    SUMMARIZE = "summarize"
    PREVIEW = "preview"
    SYNTHESIZE = "synthesize"
    MASTER = "master"
    EXPORT = "export"


class UsageConfidence(StrEnum):
    ACTUAL = "actual"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class AttemptStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BILLING_UNKNOWN = "billingUnknown"


class BillingState(StrEnum):
    NOT_SENT = "notSent"
    KNOWN = "known"
    UNKNOWN = "unknown"


class ProviderCapabilities(ContractModel):
    stream: CapabilityState
    structured: CapabilityState
    translation: CapabilityState


class Pricing(ContractModel):
    pricing_class: PricingClass = Field(alias="class")
    currency: str | None = None
    input_per_million: Decimal | None = Field(default=None, ge=0)
    output_per_million: Decimal | None = Field(default=None, ge=0)


class ModelSnapshot(ContractModel):
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    version: str | None = None
    region: str | None = None
    api_kind: ApiKind
    context_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    languages: list[str] = Field(min_length=1)
    capabilities: ProviderCapabilities
    availability: Availability
    pricing: Pricing
    license: str | None = None
    source_url: str = Field(min_length=1)
    fetched_at: datetime
    expires_at: datetime
    benchmark_ref: str | None = None

    @field_validator("expires_at")
    @classmethod
    def expiry_is_after_fetch(cls, value: datetime, info: Any) -> datetime:
        fetched_at = info.data.get("fetched_at")
        if fetched_at is not None and value < fetched_at:
            raise ValueError("expires_at must not precede fetched_at")
        return value


class PromptSegment(ContractModel):
    id: str = Field(min_length=1)
    text: str


class PromptEnvelope(ContractModel):
    builder_version: str = Field(min_length=1)
    source_language: str = Field(min_length=2)
    target_language: str = Field(min_length=2)
    style_revision_id: str = Field(min_length=1)
    expected_segment_ids: list[str] = Field(min_length=1)
    segments: list[PromptSegment] = Field(min_length=1)
    system_policy: str
    user_instruction: str
    context_snapshot_id: str = Field(min_length=1)
    estimated_input_tokens: int = Field(ge=0)
    reserved_output_tokens: int = Field(ge=0)


class ContextSelection(ContractModel):
    id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    tokens: int = Field(ge=0)


class ContextExclusion(ContractModel):
    id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ContextSnapshot(ContractModel):
    project_id: str = Field(min_length=1)
    chapter_id: str = Field(min_length=1)
    source_revision_id: str = Field(min_length=1)
    glossary_revision_ids: list[str] = Field(default_factory=list)
    memory_revision_ids: list[str] = Field(default_factory=list)
    character_revision_ids: list[str] = Field(default_factory=list)
    tm_revision_ids: list[str] = Field(default_factory=list)
    selected: list[ContextSelection] = Field(default_factory=list)
    excluded: list[ContextExclusion] = Field(default_factory=list)
    token_budget: int = Field(ge=1)
    index_revision_id: str | None = None


class ExecutionPlan(ContractModel):
    project_id: str = Field(min_length=1)
    chapter_id: str = Field(min_length=1)
    source_revision_id: str = Field(min_length=1)
    stage: Stage
    profile_id: str | None = None
    profile_revision: int | None = Field(default=None, ge=1)
    model_snapshot_id: str | None = None
    prompt_envelope_id: str | None = None
    context_snapshot_id: str | None = None
    input_revision_ids: list[str] = Field(min_length=1)
    rights_grant_id: str | None = None
    consent_id: str | None = None
    budget_authorization_id: str | None = None
    quote_id: str | None = None
    fallback_profile_ids: list[str] = Field(default_factory=list)
    idempotency_key: str = Field(min_length=1)


class ProviderError(ContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool
    billing_state: BillingState
    retry_after_ms: int | None = Field(default=None, ge=0)
    correlation_id: str = Field(min_length=1)
    details: dict[str, object] = Field(default_factory=dict)


class UsageReport(ContractModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    confidence: UsageConfidence


class AttemptResult(ContractModel):
    attempt_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    requested_model: str | None = None
    actual_model: str | None = None
    provider_request_id: str | None = None
    status: AttemptStatus
    finish_reason: str | None = None
    usage: UsageReport
    output_revision_ids: list[str] = Field(default_factory=list)
    error: ProviderError | None = None


class FallbackDecision(ContractModel):
    allowed: bool
    reason: str = Field(min_length=1)
    selected_profile_id: str | None = None
    selected_model_snapshot_id: str | None = None
    quote_id: str | None = None


SnapshotT = TypeVar("SnapshotT", bound=ContractModel)


def _canonical_value(value: object) -> object:
    if isinstance(value, BaseModel):
        value = value.model_dump(by_alias=True, mode="json")
    if isinstance(value, Mapping):
        return {
            key: _canonical_value(item)
            for key, item in value.items()
            if key not in {"id", "createdAt", "hash"}
        }
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    return value


def _canonical_payload(value: ContractModel | Mapping[str, object]) -> dict[str, object]:
    payload = _canonical_value(value)
    if not isinstance(payload, dict):
        raise TypeError("snapshot payload must be an object")
    return payload


def canonical_snapshot_hash(value: ContractModel | Mapping[str, object]) -> str:
    """Return the stable SHA-256 hash for a v1 snapshot payload."""

    encoded = json.dumps(
        _canonical_payload(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def with_snapshot_hash(value: SnapshotT) -> SnapshotT:
    """Copy a snapshot and attach its canonical hash."""

    return value.model_copy(update={"hash": canonical_snapshot_hash(value)})


def parse_snapshot(payload: Mapping[str, object], model_type: type[SnapshotT]) -> SnapshotT:
    """Validate a v1 snapshot and reject an explicitly tampered hash."""

    schema_version = payload.get("schemaVersion", payload.get("schema_version", 1))
    if schema_version != 1:
        raise ValueError(f"UNSUPPORTED_SCHEMA: expected 1, got {schema_version}")
    value = model_type.model_validate(payload)
    if value.hash is not None and value.hash.lower() != canonical_snapshot_hash(value):
        raise ValueError("SNAPSHOT_HASH_MISMATCH")
    return value
