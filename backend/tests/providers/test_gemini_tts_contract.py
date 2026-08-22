from __future__ import annotations

from pathlib import Path

import pytest

from app.contracts import OperationContext, SynthesisRequest, Usage
from app.modules.compliance.cloud import CloudCallDecision
from app.providers.gemini_tts import CapabilityMismatch, GeminiSceneSegment, GeminiTtsAdapter


def test_gemini_scene_rejects_more_than_two_speakers() -> None:
    adapter = GeminiTtsAdapter(
        project_id="project-1",
        provider_profile_id="profile-1",
        cloud_guard=GuardSpy(),
        http_client=HttpSpy({}),
    )

    with pytest.raises(CapabilityMismatch):
        adapter.build_scene_request(
            (
                GeminiSceneSegment("narrator", "Một."),
                GeminiSceneSegment("hero", "Hai."),
                GeminiSceneSegment("villain", "Ba."),
            )
        )


@pytest.mark.asyncio
async def test_usage_uses_provider_tokens_not_duration_guess(tmp_path: Path) -> None:
    adapter = GeminiTtsAdapter(
        project_id="project-1",
        provider_profile_id="profile-1",
        cloud_guard=GuardSpy(),
        http_client=HttpSpy(
            {
                "audio": {"data": "AAAA", "sampleRateHertz": 24000},
                "usageMetadata": {"inputTokenCount": 12, "audioTokenCount": 3200},
            }
        ),
    )

    result = await adapter.synthesize(_request(), tmp_path / "x.wav")

    assert {usage.unit: usage.measured_units for usage in result.usage} == {
        "INPUT_TOKEN": 12,
        "AUDIO_TOKEN": 3200,
    }
    assert (tmp_path / "x.wav").read_bytes().startswith(b"RIFF")


class GuardSpy:
    def evaluate(self, **kwargs):
        return CloudCallDecision(
            allowed=True,
            cloud_consent_id="consent-1",
            authorization_id="auth-1",
            rate_card_ids=("input-rate", "audio-rate"),
            remaining_quota=(),
            reasons=(),
        )


class HttpSpy:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def post(self, url: str, *, json: dict[str, object], headers: dict[str, str], timeout: int):
        return ResponseSpy(self.payload)


class ResponseSpy:
    status_code = 200
    headers = {"x-request-id": "gemini-request-1"}

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def json(self) -> dict[str, object]:
        return self.payload


def _request() -> SynthesisRequest:
    return SynthesisRequest(
        context=OperationContext(
            operation_id="tts:chapter:segment",
            cache_key="cache",
            timeout_seconds=30,
            estimated_units=12,
            budget_authorization_id="auth-1",
            cloud_consent_id="consent-1",
        ),
        speech_segment_id="segment-1",
        narration_text="Xin chao ban doc.",
        locale="vi-VN",
        voice_id="gemini-voice-a",
        speed="1.0",
        pitch="0",
        style=None,
        sample_rate=44_100,
    )
