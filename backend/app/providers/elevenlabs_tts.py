from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from app.contracts import SynthesisRequest, SynthesisResult, Usage, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked


ELEVEN_TTS_ENDPOINT = "https://api.elevenlabs.io/v1/text-to-speech"
MAX_STREAM_CHUNK_BYTES = 1024 * 1024


class VoiceConsentMissing(ValueError):
    """Raised when a user voice reference has no local consent artifact."""


class ProviderSchemaError(RuntimeError):
    """Raised when ElevenLabs response cannot become a canonical audio artifact."""


@dataclass(frozen=True)
class VoiceReferencePreset:
    id: str
    origin: str
    voice_id: str
    consent_evidence_artifact_id: str | None


class ElevenVoiceCatalog:
    def __init__(self, presets: tuple[VoiceReferencePreset, ...]) -> None:
        self.presets = presets

    def activate(self, preset_id: str) -> VoiceReferencePreset:
        for preset in self.presets:
            if preset.id == preset_id:
                if preset.origin == "USER_REFERENCE" and preset.consent_evidence_artifact_id is None:
                    raise VoiceConsentMissing("VOICE_CONSENT_REQUIRED")
                return preset
        raise KeyError(preset_id)


class ElevenLabsTtsAdapter:
    def __init__(
        self,
        *,
        project_id: str,
        provider_profile_id: str,
        cloud_guard: object,
        http_client: object,
        endpoint: str = ELEVEN_TTS_ENDPOINT,
        model: str = "eleven_flash_v2_5",
        provider_version: str = "v1",
        api_key: str | None = None,
    ) -> None:
        self.project_id = project_id
        self.provider_profile_id = provider_profile_id
        self.cloud_guard = cloud_guard
        self.http_client = http_client
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.provider_version = provider_version
        self.api_key = api_key

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": "elevenlabs",
            "model": self.model,
            "provider_version": self.provider_version,
            "sample_rates": [44_100],
            "formats": ["wav"],
            "network": True,
            "cost_tier": "PREMIUM",
            "usage_units": [UsageUnit.CHARACTER.value],
        }

    async def list_voices(self, locale: str) -> list[dict[str, object]]:
        return [{"id": "eleven-voice", "locale": locale, "sample_rate": 44_100}]

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
        output_path.parent.mkdir(parents=True, exist_ok=True)
        partial = output_path.with_suffix(output_path.suffix + ".partial")
        if partial.exists():
            partial.unlink()
        url = f"{self.endpoint}/{request.voice_id}/stream"
        async with await self.http_client.stream(
            "POST",
            url,
            json={
                "text": request.narration_text,
                "model_id": self.model,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
            headers=self._headers(),
            timeout=request.context.timeout_seconds,
        ) as response:
            _raise_for_status(response.status_code)
            with partial.open("wb") as file:
                async for chunk in response.aiter_bytes():
                    if len(chunk) > MAX_STREAM_CHUNK_BYTES:
                        raise ProviderSchemaError("PROVIDER_SCHEMA")
                    file.write(chunk)
        data = partial.read_bytes()
        if not data.startswith(b"RIFF"):
            raise ProviderSchemaError("PROVIDER_SCHEMA")
        partial.replace(output_path)
        request_id = _request_id(response)
        measured_usage = (Usage(UsageUnit.CHARACTER.value, len(request.narration_text), request_id),)
        return SynthesisResult(
            provider="elevenlabs",
            model=self.model,
            provider_version=self.provider_version,
            duration_ms=0,
            sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
            usage=measured_usage,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["xi-api-key"] = self.api_key
        return headers


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
