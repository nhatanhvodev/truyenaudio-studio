from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.modules.execution.contracts import CapabilityState, PricingClass
from app.providers.catalog import CatalogFilter, ProviderCatalog


def create_models_router(catalog: ProviderCatalog | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/models")
    active_catalog = catalog or ProviderCatalog()

    @router.get("")
    def list_models(
        provider_id: str | None = Query(default=None, alias="providerId"),
        language: str | None = None,
        pricing: PricingClass | None = None,
        stream: CapabilityState | None = None,
        cursor: str | None = None,
        limit: int = Query(default=25, ge=1, le=100),
    ) -> dict[str, object]:
        try:
            items, next_cursor = active_catalog.page_models(
                CatalogFilter(provider_id=provider_id, language=language, pricing=pricing, stream=stream),
                cursor=cursor,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"items": [item.model_dump(by_alias=True, mode="json") for item in items], "nextCursor": next_cursor}

    return router

