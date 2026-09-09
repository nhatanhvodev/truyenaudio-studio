from __future__ import annotations

from collections.abc import Callable, Mapping

from fastapi import APIRouter, HTTPException, Query

from app.modules.execution.contracts import CapabilityState, PricingClass
from app.providers.catalog import (
    CatalogFilter,
    DiscoveryCache,
    DiscoveryResponse,
    ProviderCatalog,
    curated_provider_catalog,
    filter_model_snapshots,
    page_model_snapshots,
)


DiscoveryFetcher = Callable[[str | None], DiscoveryResponse]


def create_models_router(
    catalog: ProviderCatalog | None = None,
    *,
    discovery_cache: DiscoveryCache | None = None,
    discovery_fetchers: Mapping[str, DiscoveryFetcher] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/models")
    active_catalog = catalog or curated_provider_catalog()
    active_fetchers = dict(discovery_fetchers or {})

    @router.get("")
    def list_models(
        profile_id: str | None = Query(default=None, alias="profileId"),
        provider_id: str | None = Query(default=None, alias="providerId"),
        language: str | None = None,
        region: str | None = None,
        pricing: PricingClass | None = None,
        stream: CapabilityState | None = None,
        structured: CapabilityState | None = None,
        translation: CapabilityState | None = None,
        context_min: int | None = Query(default=None, alias="contextMin", ge=1),
        benchmarked: bool | None = None,
        refresh: bool = False,
        cursor: str | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ) -> dict[str, object]:
        try:
            resolved_provider_id = _resolve_provider_id(active_catalog, profile_id, provider_id)
            filters = CatalogFilter(
                provider_id=resolved_provider_id,
                language=language,
                region=region,
                pricing=pricing,
                stream=stream,
                structured=structured,
                translation=translation,
                min_context_tokens=context_min,
                benchmarked=benchmarked,
            )
            discovery_state = None
            if resolved_provider_id and discovery_cache is not None:
                discovery_state = _discovery_state(
                    discovery_cache,
                    active_fetchers,
                    resolved_provider_id,
                    refresh=refresh,
                )
            if discovery_state is not None:
                filtered = filter_model_snapshots(discovery_state.items, filters)
                items, next_cursor = page_model_snapshots(filtered, cursor=cursor, limit=limit)
            else:
                items, next_cursor = active_catalog.page_models(filters, cursor=cursor, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        stale = bool(discovery_state and discovery_state.stale)
        return {
            "items": [item.model_dump(by_alias=True, mode="json") for item in items],
            "nextCursor": next_cursor,
            "stale": stale,
            "etag": discovery_state.etag if discovery_state else None,
            "notModified": bool(discovery_state and discovery_state.not_modified),
            "discoveryError": discovery_state.error if discovery_state else None,
        }

    return router


def _resolve_provider_id(
    catalog: ProviderCatalog,
    profile_id: str | None,
    provider_id: str | None,
) -> str | None:
    if profile_id is None:
        return provider_id
    mapped_provider_id = catalog.provider_for_profile(profile_id)
    resolved = mapped_provider_id or profile_id
    if provider_id is not None and provider_id != resolved:
        raise ValueError("PROFILE_PROVIDER_MISMATCH")
    return resolved


def _discovery_state(
    discovery_cache: DiscoveryCache,
    discovery_fetchers: Mapping[str, DiscoveryFetcher],
    provider_id: str,
    *,
    refresh: bool,
):
    if refresh:
        fetcher = discovery_fetchers.get(provider_id)
        if fetcher is None:
            raise RuntimeError("DISCOVERY_FETCHER_UNAVAILABLE")
        try:
            return discovery_cache.refresh(provider_id, fetcher)
        except Exception as exc:
            raise RuntimeError("DISCOVERY_UNAVAILABLE") from exc
    return discovery_cache.get(provider_id)
