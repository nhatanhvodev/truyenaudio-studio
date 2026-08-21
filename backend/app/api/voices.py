from __future__ import annotations

from dataclasses import asdict
from enum import Enum

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.modules.voices.catalog import (
    COMMON_PREVIEW_TEXT,
    LocalModelUnavailable,
    ModelLicenseUnverified,
    VoiceCatalog,
)
from app.settings.config import Settings


class PreviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    preset_id: str = Field(alias="presetId")
    text: str


def create_voices_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/voices")
    active_settings = settings or Settings()

    @router.get("")
    def list_voices(locale: str = "vi-VN") -> dict[str, object]:
        catalog = VoiceCatalog.local_defaults(active_settings.data_root)
        return {
            "previewText": COMMON_PREVIEW_TEXT,
            "voices": [_payload(voice) for voice in catalog.list(locale)],
        }

    @router.post("/preview")
    def preview(request: PreviewRequest) -> dict[str, object]:
        catalog = VoiceCatalog.local_defaults(active_settings.data_root)
        text = request.text.strip() or COMMON_PREVIEW_TEXT
        try:
            return _payload(catalog.preview(request.preset_id, text))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="VOICE_PRESET_NOT_FOUND") from exc
        except ModelLicenseUnverified as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LocalModelUnavailable as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router


def _payload(value: object) -> dict[str, object]:
    data = _convert(asdict(value))
    if "cost_tier" in data:
        data["costTier"] = data.pop("cost_tier")
    if "activation_hint" in data:
        data["activationHint"] = data.pop("activation_hint")
    if "artifact_kind" in data:
        data["artifactKind"] = data.pop("artifact_kind")
    if "cache_key" in data:
        data["cacheKey"] = data.pop("cache_key")
    if "preset_id" in data:
        data["presetId"] = data.pop("preset_id")
    return data


def _convert(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _convert(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_convert(item) for item in value]
    return value
