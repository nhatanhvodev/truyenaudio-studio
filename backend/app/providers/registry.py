"""Capability and authorization boundary for provider adapter creation."""

from __future__ import annotations

from dataclasses import dataclass

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


class ProviderRegistry:
    def __init__(self, descriptors=(), *, catalog: ProviderCatalog | None = None) -> None:
        self.catalog = catalog or ProviderCatalog(descriptors)

    def resolve(self, plan_or_snapshot: object, authorization: RegistryAuthorization) -> object:
        snapshot_id = getattr(plan_or_snapshot, "model_snapshot_id", plan_or_snapshot)
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise RegistryError("MODEL_SNAPSHOT_REQUIRED")
        if authorization.profile_revision < 1:
            raise RegistryError("PROFILE_REVISION_REQUIRED")
        if authorization.model_snapshot_id != snapshot_id:
            raise RegistryError("MODEL_SNAPSHOT_MISMATCH")

        model = next((item for item in self.catalog.list_models() if item.id == snapshot_id), None)
        if model is None or model.provider_id != authorization.profile_id:
            raise RegistryError("MODEL_UNAVAILABLE")
        descriptor = self.catalog.get(model.provider_id)
        if not descriptor.enabled:
            raise RegistryError("PROFILE_DISABLED")
        if model.availability is not Availability.AVAILABLE:
            raise RegistryError("MODEL_UNAVAILABLE")
        if model.capabilities.translation is not CapabilityState.SUPPORTED:
            raise RegistryError("CAPABILITY_UNKNOWN")
        if descriptor.factory is None:
            raise RegistryError("ADAPTER_FACTORY_MISSING")
        return descriptor.factory(authorization)

