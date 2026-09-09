"""Capability and authorization boundary for provider adapter creation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.modules.execution.contracts import Availability, CapabilityState, ModelSnapshot, Stage
from app.providers.catalog import ProviderCatalog


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class RegistryAuthorization:
    profile_id: str
    profile_revision: int
    model_snapshot_id: str
    stage: str = "translate"
    provider_id: str | None = None
    fallback_profile_ids: tuple[str, ...] = ()


class ProviderRegistry:
    def __init__(
        self,
        descriptors=(),
        *,
        catalog: ProviderCatalog | None = None,
        profile_revision_resolver: Callable[[str], int | None] | None = None,
    ) -> None:
        self.catalog = catalog or ProviderCatalog(descriptors)
        self._profile_revision_resolver = profile_revision_resolver

    def resolve(self, plan_or_snapshot: object, authorization: RegistryAuthorization) -> object:
        snapshot_id = getattr(plan_or_snapshot, "model_snapshot_id", plan_or_snapshot)
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise RegistryError("MODEL_SNAPSHOT_REQUIRED")
        self.validate_authorization(authorization)
        if authorization.model_snapshot_id != snapshot_id:
            raise RegistryError("MODEL_SNAPSHOT_MISMATCH")

        resolved = self.catalog.model_descriptor(snapshot_id)
        if resolved is None:
            raise RegistryError("MODEL_UNAVAILABLE")
        descriptor, model = resolved
        if authorization.provider_id is not None and authorization.provider_id != descriptor.provider_id:
            raise RegistryError("PROFILE_PROVIDER_MISMATCH")
        authorized_profile_ids = (authorization.profile_id, *authorization.fallback_profile_ids)
        if descriptor.profile_ids and not any(profile_id in descriptor.profile_ids for profile_id in authorized_profile_ids):
            raise RegistryError("PROFILE_NOT_REGISTERED")
        if not descriptor.enabled:
            raise RegistryError("PROFILE_DISABLED")
        if model.availability != Availability.AVAILABLE:
            raise RegistryError("MODEL_UNAVAILABLE")
        if model.expires_at <= datetime.now(UTC):
            raise RegistryError("MODEL_SNAPSHOT_EXPIRED")
        capability = _capability_for_stage(model, authorization.stage)
        if capability == CapabilityState.UNSUPPORTED:
            raise RegistryError("CAPABILITY_UNSUPPORTED")
        if capability != CapabilityState.SUPPORTED:
            raise RegistryError("CAPABILITY_UNKNOWN")
        if descriptor.factory is None:
            raise RegistryError("ADAPTER_FACTORY_MISSING")
        return descriptor.factory(authorization)

    def validate_authorization(self, authorization: RegistryAuthorization) -> None:
        if authorization.profile_revision < 1:
            raise RegistryError("PROFILE_REVISION_REQUIRED")
        if self._profile_revision_resolver is None:
            raise RegistryError("PROFILE_REVISION_UNAVAILABLE")
        try:
            current_revision = self._profile_revision_resolver(authorization.profile_id)
        except Exception as exc:
            raise RegistryError("PROFILE_REVISION_UNAVAILABLE") from exc
        if current_revision is None:
            raise RegistryError("PROFILE_REVISION_UNAVAILABLE")
        if current_revision != authorization.profile_revision:
            raise RegistryError("PROFILE_REVISION_STALE")


def _capability_for_stage(model: ModelSnapshot, stage: str) -> CapabilityState:
    try:
        normalized = Stage(stage)
    except ValueError:
        return CapabilityState.UNKNOWN
    if normalized == Stage.TRANSLATE:
        return model.capabilities.translation
    if normalized in {Stage.REVIEW, Stage.POLISH, Stage.REPAIR, Stage.SUMMARIZE}:
        return model.capabilities.structured
    return CapabilityState.UNKNOWN
