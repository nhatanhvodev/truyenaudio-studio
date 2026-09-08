"""Typed execution contracts shared by API, jobs and provider adapters."""

from .contracts import (
    AttemptResult,
    CapabilityState,
    ContextSnapshot,
    ExecutionPlan,
    FallbackDecision,
    ModelSnapshot,
    PromptEnvelope,
    ProviderError,
    UsageConfidence,
    canonical_snapshot_hash,
    parse_snapshot,
)

__all__ = [
    "AttemptResult",
    "CapabilityState",
    "ContextSnapshot",
    "ExecutionPlan",
    "FallbackDecision",
    "ModelSnapshot",
    "PromptEnvelope",
    "ProviderError",
    "UsageConfidence",
    "canonical_snapshot_hash",
    "parse_snapshot",
]
