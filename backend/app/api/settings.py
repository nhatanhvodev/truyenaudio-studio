from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.modules.settings.feature_flags import (
    FeatureFlagRejected,
    effective_flags,
    flag_catalog,
    flag_gate_report,
)
from app.modules.settings.release_manifest import load_release_manifest


class FeatureFlagRequest(BaseModel):
    flags: dict[str, bool] = Field(default_factory=dict)


def create_settings_router() -> APIRouter:
    router = APIRouter(prefix="/api/settings")

    @router.get("/feature-flags")
    def read_feature_flags() -> dict[str, object]:
        """Effective flags plus the release-manifest evidence behind each one (R01)."""
        manifest = load_release_manifest()
        return {
            "flags": flag_gate_report(manifest),
            "releaseManifest": {
                "path": str(manifest.path) if manifest.path is not None else None,
                "available": manifest.available,
                "releaseScope": manifest.release_scope,
                "commit": manifest.commit,
                "generatedAt": manifest.generated_at,
                "note": manifest.note,
            },
        }

    @router.put("/feature-flags")
    def write_feature_flags(request: FeatureFlagRequest) -> dict[str, object]:
        """Resolve a requested flag set; unsafe/unknown/evidence-gated flags are rejected.

        A flag whose release-manifest evidence is not PASS is refused with
        FEATURE_FLAG_EVIDENCE_NOT_PASS:<name>, so no API call can switch on a live
        cloud/TTS/quality feature that has not been verified (R01 / G-RELEASE).
        """
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
