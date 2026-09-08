from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.modules.execution.contracts import CapabilityState, PricingClass
from app.providers.catalog import CatalogFilter, DiscoveryCache, ProviderCatalog


def create_models_router(
    catalog: ProviderCatalog | None = None,
    *,
    discovery_cache: DiscoveryCache | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/models")
    active_catalog = catalog or ProviderCatalog()

    @router.get("")
    def list_models(
        profile_id: str | None = Query(default=None, alias="profileId"),
        language: str | None = None,
        pricing: PricingClass | None = None,
        stream: CapabilityState | None = None,
        cursor: str | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ) -> dict[str, object]:
        try:
            provider_id = active_catalog.provider_for_profile(profile_id) if profile_id else None
            if profile_id and provider_id is None:
                provider_id = profile_id
            items, next_cursor = active_catalog.page_models(
                CatalogFilter(provider_id=provider_id, language=language, pricing=pricing, stream=stream),
                cursor=cursor,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        stale = False
        if provider_id and discovery_cache is not None:
            state = discovery_cache.get(provider_id)
            stale = bool(state and state.stale)
        return {
            "items": [item.model_dump(by_alias=True, mode="json") for item in items],
            "nextCursor": next_cursor,
            "stale": stale,
        }

    return router
