from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.modules.execution.contracts import (
    ApiKind,
    Availability,
    CapabilityState,
    ModelSnapshot,
    Pricing,
    PricingClass,
    ProviderCapabilities,
)
from app.providers.catalog import CatalogFilter, ProviderDescriptor, ProviderCatalog
from app.providers.registry import ProviderRegistry, RegistryAuthorization, RegistryError


def _snapshot(
    provider_id: str,
    model_id: str,
    *,
    stream: CapabilityState = CapabilityState.SUPPORTED,
    pricing_class: PricingClass = PricingClass.PAID,
) -> ModelSnapshot:
    now = datetime.now(UTC)
    return ModelSnapshot(
        provider_id=provider_id,
        model_id=model_id,
        api_kind=ApiKind.CHAT,
        languages=["zh-CN", "vi-VN"],
        capabilities=ProviderCapabilities(
            stream=stream,
            structured=CapabilityState.SUPPORTED,
            translation=CapabilityState.SUPPORTED,
        ),
        availability=Availability.AVAILABLE,
        pricing=Pricing(pricing_class=pricing_class, currency="USD"),
        source_url="https://example.test/catalog",
        fetched_at=now,
        expires_at=now + timedelta(hours=1),
    )


def test_catalog_filters_capability_and_pricing_without_coercing_unknown() -> None:
    catalog = ProviderCatalog(
        [
            ProviderDescriptor("p1", "gemini", "adapter-a", models=[_snapshot("p1", "fast")]),
            ProviderDescriptor(
                "p2",
                "qwen",
                "adapter-b",
                models=[_snapshot("p2", "unknown", stream=CapabilityState.UNKNOWN, pricing_class=PricingClass.UNKNOWN)],
            ),
        ]
    )

    assert [model.model_id for model in catalog.list_models(CatalogFilter(pricing=PricingClass.PAID))] == ["fast"]
    assert catalog.list_models(CatalogFilter(stream=CapabilityState.SUPPORTED))[0].model_id == "fast"
    assert catalog.list_models(CatalogFilter(stream=CapabilityState.UNKNOWN))[0].model_id == "unknown"


def test_registry_requires_authorized_profile_revision_and_supported_model() -> None:
    created: list[str] = []
    descriptor = ProviderDescriptor(
        "p1",
        "gemini",
        "adapter-a",
        models=[_snapshot("p1", "fast")],
        factory=lambda authorization: created.append(authorization.profile_id) or authorization,
    )
    registry = ProviderRegistry([descriptor])
    authorization = RegistryAuthorization(profile_id="p1", profile_revision=2, model_snapshot_id=descriptor.models[0].id)

    result = registry.resolve(descriptor.models[0].id, authorization)
    assert result.profile_id == "p1"
    assert created == ["p1"]

    with pytest.raises(RegistryError, match="PROFILE_REVISION_REQUIRED"):
        registry.resolve(descriptor.models[0].id, RegistryAuthorization(profile_id="p1", profile_revision=0, model_snapshot_id="x"))
    with pytest.raises(RegistryError, match="MODEL_UNAVAILABLE"):
        registry.resolve("missing", RegistryAuthorization(profile_id="p1", profile_revision=2, model_snapshot_id="missing"))


def test_catalog_pagination_has_opaque_cursor_and_bounds() -> None:
    models = [_snapshot("p1", f"model-{index}") for index in range(3)]
    catalog = ProviderCatalog([ProviderDescriptor("p1", "provider", "adapter", models=models)])

    first, cursor = catalog.page_models(limit=2)
    second, end = catalog.page_models(cursor=cursor, limit=2)
    assert [item.model_id for item in first] == ["model-0", "model-1"]
    assert [item.model_id for item in second] == ["model-2"]
    assert end is None
    with pytest.raises(ValueError, match="CURSOR_INVALID"):
        catalog.page_models(cursor="bad", limit=2)
    with pytest.raises(ValueError, match="LIMIT_OUT_OF_RANGE"):
        catalog.page_models(limit=101)
