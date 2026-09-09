from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.models import create_models_router
from app.modules.execution.contracts import (
    ApiKind,
    Availability,
    CapabilityState,
    ModelSnapshot,
    Pricing,
    PricingClass,
    ProviderCapabilities,
)
from app.providers.catalog import DiscoveryCache, DiscoveryResponse, ProviderCatalog, ProviderDescriptor


NOW = datetime(2026, 9, 8, tzinfo=UTC)


def _snapshot(
    provider_id: str,
    model_id: str,
    *,
    context_tokens: int | None = 8_192,
    pricing_class: PricingClass = PricingClass.UNKNOWN,
    translation: CapabilityState = CapabilityState.SUPPORTED,
    structured: CapabilityState = CapabilityState.SUPPORTED,
    benchmark_ref: str | None = None,
) -> ModelSnapshot:
    return ModelSnapshot(
        id=f"{provider_id}-{model_id}",
        provider_id=provider_id,
        model_id=model_id,
        api_kind=ApiKind.CHAT,
        context_tokens=context_tokens,
        languages=["zh-CN", "vi-VN"],
        capabilities=ProviderCapabilities(
            stream=CapabilityState.SUPPORTED,
            structured=structured,
            translation=translation,
        ),
        availability=Availability.AVAILABLE,
        pricing=Pricing(pricing_class=pricing_class, currency="USD"),
        source_url=f"https://example.test/{provider_id}/{model_id}",
        fetched_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        benchmark_ref=benchmark_ref,
    )


def _client(
    catalog: ProviderCatalog | None = None,
    *,
    discovery_cache: DiscoveryCache | None = None,
    discovery_fetchers=None,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_models_router(
            catalog,
            discovery_cache=discovery_cache,
            discovery_fetchers=discovery_fetchers,
        )
    )
    return TestClient(app)


def test_models_api_uses_profile_id_pagination_and_stale_state() -> None:
    model = _snapshot("gemini", "flash")
    catalog = ProviderCatalog(
        [ProviderDescriptor("gemini", "Gemini", "gemini", models=[model], profile_ids=["profile-1"])]
    )

    with _client(catalog) as client:
        response = client.get("/api/models", params={"profileId": "profile-1"})

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["modelId"] == "flash"
    assert body["stale"] is False
    assert body["etag"] is None
    assert body["notModified"] is False
    assert body["discoveryError"] is None


def test_models_api_filters_capability_context_pricing_and_quality_provenance() -> None:
    catalog = ProviderCatalog(
        [
            ProviderDescriptor(
                "gemini",
                "Gemini",
                "gemini",
                models=[
                    _snapshot("gemini", "short", context_tokens=4_096, pricing_class=PricingClass.PAID),
                    _snapshot(
                        "gemini",
                        "long-benchmarked",
                        context_tokens=128_000,
                        benchmark_ref="bench-001",
                    ),
                    _snapshot(
                        "gemini",
                        "chat-only",
                        context_tokens=128_000,
                        translation=CapabilityState.UNSUPPORTED,
                    ),
                ],
            )
        ]
    )

    with _client(catalog) as client:
        response = client.get(
            "/api/models",
            params={
                "providerId": "gemini",
                "translation": "supported",
                "contextMin": 64000,
                "benchmarked": True,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert [item["modelId"] for item in body["items"]] == ["long-benchmarked"]
    assert body["items"][0]["benchmarkRef"] == "bench-001"


def test_models_api_does_not_return_unknown_price_as_free() -> None:
    catalog = ProviderCatalog(
        [
            ProviderDescriptor(
                "gemini",
                "Gemini",
                "gemini",
                models=[
                    _snapshot("gemini", "unknown-price", pricing_class=PricingClass.UNKNOWN),
                    _snapshot("gemini", "paid", pricing_class=PricingClass.PAID),
                ],
            )
        ]
    )

    with _client(catalog) as client:
        response = client.get("/api/models", params={"providerId": "gemini", "pricing": "free"})

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_models_api_refresh_uses_cached_snapshot_not_empty_success_on_outage() -> None:
    cache = DiscoveryCache(cooldown=timedelta(seconds=0))
    cached = _snapshot("gemini", "cached")
    cache.refresh("gemini", lambda etag: DiscoveryResponse((cached,), "etag-1"), now=NOW)

    def outage(etag: str | None) -> DiscoveryResponse:
        raise RuntimeError("provider returned 500")

    with _client(discovery_cache=cache, discovery_fetchers={"gemini": outage}) as client:
        response = client.get("/api/models", params={"providerId": "gemini", "refresh": True})

    assert response.status_code == 200
    body = response.json()
    assert [item["modelId"] for item in body["items"]] == ["cached"]
    assert body["stale"] is True
    assert body["discoveryError"] == "DISCOVERY_UNAVAILABLE"


def test_models_api_refresh_without_snapshot_reports_dependency_error() -> None:
    def outage(etag: str | None) -> DiscoveryResponse:
        raise RuntimeError("provider returned 500")

    with _client(
        discovery_cache=DiscoveryCache(cooldown=timedelta(seconds=0)),
        discovery_fetchers={"gemini": outage},
    ) as client:
        response = client.get("/api/models", params={"providerId": "gemini", "refresh": True})

    assert response.status_code == 503
    assert response.json()["detail"] == "DISCOVERY_UNAVAILABLE"


def test_models_api_refresh_exposes_etag_not_modified_and_pages_cached_items() -> None:
    cache = DiscoveryCache(cooldown=timedelta(seconds=0))
    first = _snapshot("gemini", "a")
    second = _snapshot("gemini", "b")
    cache.refresh("gemini", lambda etag: DiscoveryResponse((first, second), "etag-1"), now=NOW)

    def not_modified(etag: str | None) -> DiscoveryResponse:
        assert etag == "etag-1"
        return DiscoveryResponse(not_modified=True)

    with _client(discovery_cache=cache, discovery_fetchers={"gemini": not_modified}) as client:
        response = client.get(
            "/api/models",
            params={"providerId": "gemini", "refresh": True, "limit": 1},
        )

    assert response.status_code == 200
    body = response.json()
    assert [item["modelId"] for item in body["items"]] == ["a"]
    assert body["nextCursor"]
    assert body["etag"] == "etag-1"
    assert body["notModified"] is True
    assert body["stale"] is False


def test_models_api_default_catalog_has_curated_provenance() -> None:
    with _client() as client:
        response = client.get("/api/models", params={"providerId": "gemini"})

    assert response.status_code == 200
    body = response.json()
    assert body["items"]
    assert body["items"][0]["sourceUrl"]
    assert body["items"][0]["fetchedAt"]
