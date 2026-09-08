"""Capability and authorization boundary for provider adapter creation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.modules.execution.contracts import Availability, CapabilityState
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

        model = next((item for item in self.catalog.list_models() if item.id == snapshot_id), None)
        if model is None:
            raise RegistryError("MODEL_UNAVAILABLE")
        descriptor = self.catalog.get(model.provider_id)
        if authorization.provider_id is not None and authorization.provider_id != descriptor.provider_id:
            raise RegistryError("PROFILE_PROVIDER_MISMATCH")
        if descriptor.profile_ids and authorization.profile_id not in descriptor.profile_ids:
            raise RegistryError("PROFILE_NOT_REGISTERED")
        if not descriptor.enabled:
            raise RegistryError("PROFILE_DISABLED")
        if model.availability != Availability.AVAILABLE:
            raise RegistryError("MODEL_UNAVAILABLE")
        if model.expires_at <= datetime.now(UTC):
            raise RegistryError("MODEL_SNAPSHOT_EXPIRED")
        if model.capabilities.translation != CapabilityState.SUPPORTED:
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
