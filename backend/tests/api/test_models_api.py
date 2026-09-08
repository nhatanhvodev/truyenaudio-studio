from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.models import create_models_router
from app.modules.execution.contracts import ApiKind, Availability, CapabilityState, ModelSnapshot, Pricing, PricingClass, ProviderCapabilities
from app.providers.catalog import ProviderCatalog, ProviderDescriptor


def test_models_api_uses_profile_id_pagination_and_stale_state() -> None:
    now = datetime.now(UTC)
    model = ModelSnapshot(
        provider_id="gemini",
        model_id="flash",
        api_kind=ApiKind.CHAT,
        languages=["zh-CN", "vi-VN"],
        capabilities=ProviderCapabilities(
            stream=CapabilityState.SUPPORTED,
            structured=CapabilityState.SUPPORTED,
            translation=CapabilityState.SUPPORTED,
        ),
        availability=Availability.AVAILABLE,
        pricing=Pricing(pricing_class=PricingClass.PAID, currency="USD"),
        source_url="https://example.test/model",
        fetched_at=now,
        expires_at=now + timedelta(hours=1),
    )
    app = FastAPI()
    app.include_router(create_models_router(ProviderCatalog([ProviderDescriptor("gemini", "Gemini", "gemini", models=[model], profile_ids=["profile-1"])]) ))
    with TestClient(app) as client:
        response = client.get("/api/models", params={"profileId": "profile-1"})
    assert response.status_code == 200
    assert response.json()["items"][0]["modelId"] == "flash"
    assert response.json()["stale"] is False
