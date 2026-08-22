from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from app.contracts import SynthesisRequest, SynthesisResult, Usage, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked


GOOGLE_TTS_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"


class ProviderBillingUnknown(RuntimeError):
    """Raised when a request may have reached the provider but billing is unknown."""


class ProviderSchemaError(RuntimeError):
    """Raised when a provider response does not match the recorded contract."""


class GoogleTtsAdapter:
    def __init__(
        self,
        *,
        project_id: str,
        provider_profile_id: str,
        cloud_guard: object,
        http_client: object,
        endpoint: str = GOOGLE_TTS_ENDPOINT,
        model: str = "neural2",
        provider_version: str = "v1",
        region: str = "global",
        api_key: str | None = None,
    ) -> None:
        self.project_id = project_id
        self.provider_profile_id = provider_profile_id
        self.cloud_guard = cloud_guard
        self.http_client = http_client
        self.endpoint = endpoint
        self.model = model
        self.provider_version = provider_version
        self.region = region
        self.api_key = api_key

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": "google",
            "model": self.model,
            "provider_version": self.provider_version,
            "sample_rates": [44_100],
            "formats": ["wav"],
            "network": True,
            "usage_units": [UsageUnit.CHARACTER.value],
        }

    async def list_voices(self, locale: str) -> list[dict[str, object]]:
        return [{"id": "vi-VN-Neural2-A", "locale": locale, "sample_rate": 44_100}]

    async def synthesize(self, request: SynthesisRequest, output_path: Path) -> SynthesisResult:
        usage = (Usage(UsageUnit.CHARACTER.value, len(request.narration_text)),)
        decision = self.cloud_guard.evaluate(
            project_id=self.project_id,
            provider_profile_id=self.provider_profile_id,
            operation_id=request.context.operation_id,
            estimated_usage=usage,
            category=request.context.billing_category,
            cloud_consent_id=request.context.cloud_consent_id,
            budget_authorization_id=request.context.budget_authorization_id,
        )
        if not decision.allowed:
            raise CloudCallBlocked(decision.reasons)

        try:
            response = await self.http_client.post(
                self.endpoint,
                json=_google_payload(request),
                headers=self._headers(),
                timeout=request.context.timeout_seconds,
            )
        except TimeoutError as exc:
            raise ProviderBillingUnknown("PROVIDER_NETWORK") from exc
        except OSError as exc:
            raise RuntimeError("PROVIDER_NETWORK") from exc

        _raise_for_status(response.status_code)
        payload = response.json()
        audio_content = payload.get("audioContent") if isinstance(payload, dict) else None
        if not isinstance(audio_content, str):
            raise ProviderSchemaError("PROVIDER_SCHEMA")
        audio_bytes = base64.b64decode(audio_content)
        if not audio_bytes.startswith(b"RIFF"):
            raise ProviderSchemaError("PROVIDER_SCHEMA")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        partial = output_path.with_suffix(output_path.suffix + ".partial")
        partial.write_bytes(audio_bytes)
        partial.replace(output_path)
        request_id = _request_id(response)
        measured_usage = (Usage(UsageUnit.CHARACTER.value, len(request.narration_text), request_id),)
        _commit_usage(self.cloud_guard, decision.authorization_id, measured_usage, self.model, self.region)
        return SynthesisResult(
            provider="google",
            model=self.model,
            provider_version=self.provider_version,
            duration_ms=0,
            sha256=hashlib.sha256(audio_bytes).hexdigest(),
            usage=measured_usage,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


def _google_payload(request: SynthesisRequest) -> dict[str, Any]:
    return {
        "input": {"text": request.narration_text},
        "voice": {"languageCode": request.locale, "name": request.voice_id},
        "audioConfig": {
            "audioEncoding": "LINEAR16",
            "sampleRateHertz": request.sample_rate,
            "speakingRate": float(request.speed),
            "pitch": float(request.pitch),
        },
    }


def _raise_for_status(status_code: int) -> None:
    if status_code in {401, 403}:
        raise RuntimeError("PROVIDER_AUTH")
    if status_code == 429:
        raise RuntimeError("PROVIDER_RATE_LIMIT")
    if status_code >= 500:
        raise RuntimeError("PROVIDER_NETWORK")
    if status_code >= 400:
        raise RuntimeError("PROVIDER_SCHEMA")


def _request_id(response: object) -> str | None:
    headers = getattr(response, "headers", {}) or {}
    return headers.get("x-request-id") or headers.get("X-Request-Id")


def _commit_usage(
    cloud_guard: object,
    authorization_id: str | None,
    usage: tuple[Usage, ...],
    model: str,
    region: str,
) -> None:
    if authorization_id is None or not hasattr(cloud_guard, "budget_guard"):
        return
    cloud_guard.budget_guard.commit_usage(
        authorization_id,
        provider="google",
        model=model,
        region=region,
        usage=usage,
    )
