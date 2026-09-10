from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.modules.settings.feature_flags import (
    FeatureFlagRejected,
    effective_flags,
    flag_catalog,
)


class FeatureFlagRequest(BaseModel):
    flags: dict[str, bool] = Field(default_factory=dict)


def create_settings_router() -> APIRouter:
    router = APIRouter(prefix="/api/settings")

    @router.get("/feature-flags")
    def read_feature_flags() -> dict[str, object]:
        resolved = effective_flags()
        return {
            "flags": [
                {
                    "name": flag.name,
                    "safe": flag.safe,
                    "default": flag.default,
                    "enabled": resolved[flag.name],
                    "description": flag.description,
                }
                for flag in flag_catalog()
            ]
        }

    @router.put("/feature-flags")
    def write_feature_flags(request: FeatureFlagRequest) -> dict[str, object]:
        """Resolve a requested flag set; unsafe/unknown flags are rejected (U10)."""
        try:
            resolved = effective_flags(request.flags)
        except FeatureFlagRejected as exc:
            raise HTTPException(status_code=400, detail=exc.code) from exc
        return {
            "applied": False,
            "note": "Flag chưa được lưu bền vững; rollout theo flag thuộc R01.",
            "flags": resolved,
        }

    return router
