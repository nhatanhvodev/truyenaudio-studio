from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.db.base import create_engine_for, session_factory
from app.db.models import VoicePreviewTextKind
from app.modules.voices.catalog import (
    COMMON_PREVIEW_TEXT,
    LocalModelUnavailable,
    ModelLicenseUnverified,
    VoiceCatalog,
)
from app.modules.voices.preview import (
    AdapterFactory,
    PREVIEW_JOB_NOT_FOUND,
    PreviewContentUnavailable,
    PreviewJobMissing,
    PreviewRequestInvalid,
    VoicePreviewJobView,
    VoicePreviewService,
    VoicePresetMissing,
    VOICE_PRESET_NOT_FOUND,
)
from app.settings.config import Settings


class PreviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    preset_id: str = Field(alias="presetId")
    text: str


class PreviewJobRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    preset_id: str = Field(alias="presetId")
    text: str
    text_kind: VoicePreviewTextKind = Field(
        default=VoicePreviewTextKind.CUSTOM, alias="textKind"
    )


class PreviewCompareRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    preset_ids: list[str] = Field(alias="presetIds")
    text: str


def create_voices_router(
    settings: Settings | None = None,
    *,
    adapter_factory: AdapterFactory | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/voices")
    active_settings = settings or Settings()

    def preview_service(request: Request) -> Iterator[VoicePreviewService]:
        """Durable preview service; a caller may inject a fake adapter through app.state."""
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        override = adapter_factory or getattr(
            request.app.state, "voice_preview_adapter_factory", None
        )
        run_inline = bool(getattr(request.app.state, "voice_preview_run_inline", True))
        try:
            with factory() as session:
                yield VoicePreviewService(
                    session,
                    catalog=VoiceCatalog.local_defaults(active_settings.data_root),
                    artifact_root=active_settings.data_root / "artifacts",
                    adapter_factory=override,
                    run_inline=run_inline,
                )
        finally:
            engine.dispose()

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

    @router.post("/preview-jobs", status_code=201)
    def create_preview_job(
        payload: PreviewJobRequest,
        service: VoicePreviewService = Depends(preview_service),
    ) -> dict[str, object]:
        try:
            view = service.request_preview(
                payload.preset_id, payload.text, payload.text_kind.value
            )
        except VoicePresetMissing as exc:
            raise HTTPException(status_code=404, detail=VOICE_PRESET_NOT_FOUND) from exc
        except PreviewRequestInvalid as exc:
            raise HTTPException(status_code=400, detail=exc.code) from exc
        return _job_payload(view)

    @router.post("/preview-jobs/compare")
    def compare_preview_jobs(
        payload: PreviewCompareRequest,
        service: VoicePreviewService = Depends(preview_service),
    ) -> dict[str, object]:
        try:
            compared = service.compare(payload.preset_ids, payload.text)
        except VoicePresetMissing as exc:
            raise HTTPException(status_code=404, detail=VOICE_PRESET_NOT_FOUND) from exc
        except PreviewRequestInvalid as exc:
            raise HTTPException(status_code=400, detail=exc.code) from exc
        return {
            "text": compared.text,
            "textSha256": compared.text_sha256,
            "jobs": [_job_payload(job) for job in compared.jobs],
        }

    @router.get("/preview-jobs/{job_id}")
    def get_preview_job(
        job_id: str,
        service: VoicePreviewService = Depends(preview_service),
    ) -> dict[str, object]:
        try:
            return _job_payload(service.get_job(job_id))
        except PreviewJobMissing as exc:
            raise _job_not_found() from exc

    @router.post("/preview-jobs/{job_id}/cancel")
    def cancel_preview_job(
        job_id: str,
        service: VoicePreviewService = Depends(preview_service),
    ) -> dict[str, object]:
        try:
            return _job_payload(service.cancel_job(job_id))
        except PreviewJobMissing as exc:
            raise _job_not_found() from exc

    @router.post("/preview-jobs/{job_id}/retry")
    def retry_preview_job(
        job_id: str,
        service: VoicePreviewService = Depends(preview_service),
    ) -> dict[str, object]:
        try:
            return _job_payload(service.retry_job(job_id))
        except PreviewJobMissing as exc:
            raise _job_not_found() from exc

    @router.get("/preview-jobs/{job_id}/content")
    def preview_job_content(
        job_id: str,
        service: VoicePreviewService = Depends(preview_service),
    ) -> Response:
        try:
            audio = service.read_content(job_id)
        except PreviewJobMissing as exc:
            raise HTTPException(status_code=404, detail=PREVIEW_JOB_NOT_FOUND) from exc
        except PreviewContentUnavailable as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc
        return Response(content=audio, media_type="audio/wav")

    return router


def _job_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=PREVIEW_JOB_NOT_FOUND)


def _job_payload(view: VoicePreviewJobView) -> dict[str, object]:
    return {
        "jobId": view.job_id,
        "presetId": view.preset_id,
        "textKind": view.text_kind,
        "status": view.status,
        "cacheKey": view.cache_key,
        "fromCache": view.from_cache,
        "audioUrl": view.audio_url,
        "durationMs": view.duration_ms,
        "reason": view.reason,
    }


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
