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
from app.providers.catalog import CatalogFilter, DiscoveryCache, DiscoveryResponse, ProviderDescriptor, ProviderCatalog
from app.providers.catalog import curated_provider_catalog
from app.providers.registry import ProviderRegistry, RegistryAuthorization, RegistryError


def _snapshot(
    provider_id: str,
    model_id: str,
    *,
    stream: CapabilityState = CapabilityState.SUPPORTED,
    structured: CapabilityState = CapabilityState.SUPPORTED,
    translation: CapabilityState = CapabilityState.SUPPORTED,
    pricing_class: PricingClass = PricingClass.PAID,
    region: str | None = None,
    context_tokens: int | None = None,
    benchmark_ref: str | None = None,
) -> ModelSnapshot:
    now = datetime.now(UTC)
    return ModelSnapshot(
        provider_id=provider_id,
        model_id=model_id,
        region=region,
        api_kind=ApiKind.CHAT,
        context_tokens=context_tokens,
        languages=["zh-CN", "vi-VN"],
        capabilities=ProviderCapabilities(
            stream=stream,
            structured=structured,
            translation=translation,
        ),
        availability=Availability.AVAILABLE,
        pricing=Pricing(pricing_class=pricing_class, currency="USD"),
        source_url="https://example.test/catalog",
        fetched_at=now,
        expires_at=now + timedelta(hours=1),
        benchmark_ref=benchmark_ref,
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
    assert catalog.list_models(CatalogFilter(pricing=PricingClass.FREE)) == []


def test_catalog_filters_region_context_translation_and_quality_provenance() -> None:
    catalog = ProviderCatalog(
        [
            ProviderDescriptor(
                "p1",
                "gemini",
                "adapter-a",
                models=[
                    _snapshot(
                        "p1",
                        "short",
                        context_tokens=4_096,
                        region="us",
                        benchmark_ref=None,
                    ),
                    _snapshot(
                        "p1",
                        "long-benchmarked",
                        context_tokens=128_000,
                        region="asia",
                        benchmark_ref="bench-run-001",
                    ),
                    _snapshot(
                        "p1",
                        "chat-only",
                        translation=CapabilityState.UNSUPPORTED,
                        context_tokens=128_000,
                        region="asia",
                    ),
                ],
            )
        ]
    )

    assert [model.model_id for model in catalog.list_models(CatalogFilter(region="asia"))] == [
        "chat-only",
        "long-benchmarked",
    ]
    assert [model.model_id for model in catalog.list_models(CatalogFilter(min_context_tokens=64_000))] == [
        "chat-only",
        "long-benchmarked",
    ]
    assert [model.model_id for model in catalog.list_models(CatalogFilter(translation=CapabilityState.SUPPORTED))] == [
        "long-benchmarked",
        "short",
    ]
    assert [model.model_id for model in catalog.list_models(CatalogFilter(benchmarked=True))] == ["long-benchmarked"]


def test_registry_requires_authorized_profile_revision_and_supported_model() -> None:
    created: list[str] = []
    descriptor = ProviderDescriptor(
        "p1",
        "gemini",
        "adapter-a",
        models=[_snapshot("p1", "fast")],
        factory=lambda authorization: created.append(authorization.profile_id) or authorization,
    )
    registry = ProviderRegistry([descriptor], profile_revision_resolver=lambda profile_id: 2)
    authorization = RegistryAuthorization(profile_id="p1", profile_revision=2, model_snapshot_id=descriptor.models[0].id)

    result = registry.resolve(descriptor.models[0].id, authorization)
    assert result.profile_id == "p1"
    assert created == ["p1"]

    with pytest.raises(RegistryError, match="PROFILE_REVISION_REQUIRED"):
        registry.resolve(descriptor.models[0].id, RegistryAuthorization(profile_id="p1", profile_revision=0, model_snapshot_id="x"))
    with pytest.raises(RegistryError, match="MODEL_UNAVAILABLE"):
        registry.resolve("missing", RegistryAuthorization(profile_id="p1", profile_revision=2, model_snapshot_id="missing"))


def test_registry_rejects_disabled_unknown_unsupported_and_unregistered_profiles() -> None:
    disabled = ProviderDescriptor(
        "disabled",
        "disabled",
        "adapter-a",
        models=[_snapshot("disabled", "m1")],
        enabled=False,
        factory=lambda authorization: authorization,
    )
    unsupported = ProviderDescriptor(
        "unsupported",
        "unsupported",
        "adapter-b",
        models=[_snapshot("unsupported", "m2", translation=CapabilityState.UNSUPPORTED)],
        profile_ids=["profile-1"],
        factory=lambda authorization: authorization,
    )
    unknown = ProviderDescriptor(
        "unknown",
        "unknown",
        "adapter-c",
        models=[_snapshot("unknown", "m3", structured=CapabilityState.UNKNOWN)],
        factory=lambda authorization: authorization,
    )
    registry = ProviderRegistry(
        [disabled, unsupported, unknown],
        profile_revision_resolver=lambda profile_id: 1,
    )

    with pytest.raises(RegistryError, match="PROFILE_DISABLED"):
        registry.resolve(disabled.models[0].id, RegistryAuthorization("disabled", 1, disabled.models[0].id))
    with pytest.raises(RegistryError, match="PROFILE_NOT_REGISTERED"):
        registry.resolve(unsupported.models[0].id, RegistryAuthorization("profile-2", 1, unsupported.models[0].id))
    with pytest.raises(RegistryError, match="CAPABILITY_UNSUPPORTED"):
        registry.resolve(unsupported.models[0].id, RegistryAuthorization("profile-1", 1, unsupported.models[0].id))
    with pytest.raises(RegistryError, match="CAPABILITY_UNKNOWN"):
        registry.resolve(unknown.models[0].id, RegistryAuthorization("unknown", 1, unknown.models[0].id, stage="review"))


def test_registry_allows_explicit_fallback_profile_for_descriptor() -> None:
    descriptor = ProviderDescriptor(
        "p1",
        "gemini",
        "adapter-a",
        models=[_snapshot("p1", "fast")],
        profile_ids=["fallback-1"],
        factory=lambda authorization: authorization,
    )
    registry = ProviderRegistry([descriptor], profile_revision_resolver=lambda profile_id: 1)
    authorization = RegistryAuthorization(
        profile_id="primary-1",
        profile_revision=1,
        model_snapshot_id=descriptor.models[0].id,
        fallback_profile_ids=("fallback-1",),
    )

    assert registry.resolve(descriptor.models[0].id, authorization) == authorization


def test_registry_rejects_stale_profile_revision_before_factory_dispatch() -> None:
    revisions = {"p1": 1}
    dispatched: list[str] = []
    descriptor = ProviderDescriptor(
        "p1",
        "gemini",
        "adapter-a",
        models=[_snapshot("p1", "fast")],
        factory=lambda authorization: dispatched.append(authorization.profile_id) or authorization,
    )
    registry = ProviderRegistry(
        [descriptor],
        profile_revision_resolver=lambda profile_id: revisions.get(profile_id),
    )
    authorization = RegistryAuthorization(profile_id="p1", profile_revision=1, model_snapshot_id=descriptor.models[0].id)

    registry.resolve(descriptor.models[0].id, authorization)
    revisions["p1"] = 2
    with pytest.raises(RegistryError, match="PROFILE_REVISION_STALE"):
        registry.resolve(descriptor.models[0].id, authorization)

    assert dispatched == ["p1"]


def test_registry_fails_closed_when_current_profile_revision_is_not_available() -> None:
    descriptor = ProviderDescriptor(
        "p1",
        "gemini",
        "adapter-a",
        models=[_snapshot("p1", "fast")],
        factory=lambda authorization: authorization,
    )
    registry = ProviderRegistry([descriptor])
    authorization = RegistryAuthorization(profile_id="p1", profile_revision=1, model_snapshot_id=descriptor.models[0].id)

    with pytest.raises(RegistryError, match="PROFILE_REVISION_UNAVAILABLE"):
        registry.resolve(descriptor.models[0].id, authorization)


def test_registry_converts_revision_resolver_failures_to_unavailable() -> None:
    descriptor = ProviderDescriptor(
        "p1", "gemini", "adapter-a", models=[_snapshot("p1", "fast")], factory=lambda authorization: authorization
    )
    registry = ProviderRegistry(
        [descriptor], profile_revision_resolver=lambda profile_id: (_ for _ in ()).throw(RuntimeError("database offline"))
    )
    authorization = RegistryAuthorization(profile_id="p1", profile_revision=1, model_snapshot_id=descriptor.models[0].id)

    with pytest.raises(RegistryError, match="PROFILE_REVISION_UNAVAILABLE"):
        registry.resolve(descriptor.models[0].id, authorization)


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


def test_discovery_cache_preserves_last_good_snapshot_on_outage_and_honors_cooldown() -> None:
    cache = DiscoveryCache()
    first_time = datetime(2026, 9, 8, tzinfo=UTC)
    calls: list[str | None] = []
    model = _snapshot("p1", "m1")

    def fetch(etag: str | None) -> DiscoveryResponse:
        calls.append(etag)
        return DiscoveryResponse((model,), "etag-1")

    assert cache.refresh("p1", fetch, now=first_time).stale is False
    assert cache.refresh("p1", fetch, now=first_time + timedelta(seconds=5)).items == (model,)
    assert calls == [None]

    def outage(etag: str | None) -> DiscoveryResponse:
        raise RuntimeError("offline")

    stale = cache.refresh("p1", outage, now=first_time + timedelta(minutes=2))
    assert stale.items == (model,)
    assert stale.stale is True
    assert stale.error == "DISCOVERY_UNAVAILABLE"


def test_discovery_cache_uses_etag_not_modified_without_erasing_snapshot() -> None:
    cache = DiscoveryCache()
    first_time = datetime(2026, 9, 8, tzinfo=UTC)
    model = _snapshot("p1", "m1")

    cache.refresh("p1", lambda etag: DiscoveryResponse((model,), "etag-1"), now=first_time)
    state = cache.refresh(
        "p1",
        lambda etag: DiscoveryResponse(not_modified=True),
        now=first_time + timedelta(minutes=2),
    )

    assert state.items == (model,)
    assert state.etag == "etag-1"
    assert state.not_modified is True
    assert state.stale is False


def test_curated_catalog_has_source_dates_and_does_not_mark_unknown_price_free() -> None:
    catalog = curated_provider_catalog(now=datetime(2026, 9, 8, tzinfo=UTC))
    models = catalog.list_models()

    assert {model.provider_id for model in models} >= {"gemini", "qwen", "openrouter"}
    assert all(model.source_url for model in models)
    assert all(model.fetched_at == datetime(2026, 9, 8, tzinfo=UTC) for model in models)
    assert catalog.list_models(CatalogFilter(pricing=PricingClass.FREE)) == []
