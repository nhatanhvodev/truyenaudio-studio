from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
from pathlib import Path
import wave

from app.contracts import SynthesisRequest, SynthesisResult, Usage, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked


GEMINI_TTS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-tts:generateSpeech"


class CapabilityMismatch(ValueError):
    """Raised when a requested Gemini preview exceeds adapter capability."""


class ProviderSchemaError(RuntimeError):
    """Raised when Gemini response is missing audio or usage fields."""


@dataclass(frozen=True)
class GeminiSceneSegment:
    speaker_key: str
    text: str


class GeminiTtsAdapter:
    def __init__(
        self,
        *,
        project_id: str,
        provider_profile_id: str,
        cloud_guard: object,
        http_client: object,
        endpoint: str = GEMINI_TTS_ENDPOINT,
        model: str = "gemini-flash-tts",
        provider_version: str = "v1beta",
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
            "provider": "gemini",
            "model": self.model,
            "provider_version": self.provider_version,
            "sample_rates": [44_100],
            "formats": ["wav"],
            "network": True,
            "scene_preview_speakers": 2,
            "usage_units": [UsageUnit.INPUT_TOKEN.value, UsageUnit.AUDIO_TOKEN.value],
        }

    async def list_voices(self, locale: str) -> list[dict[str, object]]:
        return [{"id": "gemini-voice-a", "locale": locale, "sample_rate": 44_100}]

    def build_scene_request(self, segments: tuple[GeminiSceneSegment, ...]) -> dict[str, object]:
        speaker_count = len({segment.speaker_key for segment in segments})
        if speaker_count > 2:
            raise CapabilityMismatch("GEMINI_SCENE_MAX_TWO_SPEAKERS")
        return {
            "speakers": sorted({segment.speaker_key for segment in segments}),
            "turns": [
                {"speaker": segment.speaker_key, "text": _trusted_text(segment.text)}
                for segment in segments
            ],
        }

    async def synthesize(self, request: SynthesisRequest, output_path: Path) -> SynthesisResult:
        estimated_usage = (Usage(UsageUnit.INPUT_TOKEN.value, max(1, request.context.estimated_units)),)
        decision = self.cloud_guard.evaluate(
            project_id=self.project_id,
            provider_profile_id=self.provider_profile_id,
            operation_id=request.context.operation_id,
            estimated_usage=estimated_usage,
            category=request.context.billing_category,
            cloud_consent_id=request.context.cloud_consent_id,
            budget_authorization_id=request.context.budget_authorization_id,
        )
        if not decision.allowed:
            raise CloudCallBlocked(decision.reasons)

        response = await self.http_client.post(
            self.endpoint,
            json={
                "model": self.model,
                "voice": request.voice_id,
                "text": _trusted_text(request.narration_text),
            },
            headers=self._headers(),
            timeout=request.context.timeout_seconds,
        )
        _raise_for_status(response.status_code)
        payload = response.json()
        audio = payload.get("audio") if isinstance(payload, dict) else None
        usage_metadata = payload.get("usageMetadata") if isinstance(payload, dict) else None
        if not isinstance(audio, dict) or not isinstance(usage_metadata, dict):
            raise ProviderSchemaError("PROVIDER_SCHEMA")
        audio_data = audio.get("data")
        sample_rate = audio.get("sampleRateHertz")
        input_tokens = usage_metadata.get("inputTokenCount")
        audio_tokens = usage_metadata.get("audioTokenCount")
        if not isinstance(audio_data, str) or not isinstance(sample_rate, int):
            raise ProviderSchemaError("PROVIDER_SCHEMA")
        if not isinstance(input_tokens, int) or not isinstance(audio_tokens, int):
            raise ProviderSchemaError("PROVIDER_SCHEMA")
        pcm = base64.b64decode(audio_data)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_wav(output_path, pcm, sample_rate)
        request_id = _request_id(response)
        measured_usage = (
            Usage(UsageUnit.INPUT_TOKEN.value, input_tokens, request_id),
            Usage(UsageUnit.AUDIO_TOKEN.value, audio_tokens, request_id),
        )
        _commit_usage(self.cloud_guard, decision.authorization_id, measured_usage, self.model, self.region)
        return SynthesisResult(
            provider="gemini",
            model=self.model,
            provider_version=self.provider_version,
            duration_ms=0,
            sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
            usage=measured_usage,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


def _trusted_text(text: str) -> str:
    return text.replace("\x00", "").strip()


def _write_wav(path: Path, pcm: bytes, sample_rate: int) -> None:
    partial = path.with_suffix(path.suffix + ".partial")
    with wave.open(str(partial), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    partial.replace(path)


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
        provider="gemini",
        model=model,
        region=region,
        usage=usage,
    )
