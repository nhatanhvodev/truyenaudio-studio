from __future__ import annotations

import pytest

from app.contracts import OperationContext, SynthesisRequest, Usage
from app.modules.compliance.cloud import CloudCallDecision
from app.providers.elevenlabs_tts import ElevenLabsTtsAdapter, ElevenVoiceCatalog, VoiceConsentMissing, VoiceReferencePreset


def test_user_reference_without_voice_consent_is_inactive() -> None:
    catalog = ElevenVoiceCatalog(
        (
            VoiceReferencePreset(
                id="ref-1",
                origin="USER_REFERENCE",
                voice_id="eleven-voice",
                consent_evidence_artifact_id=None,
            ),
        )
    )

    with pytest.raises(VoiceConsentMissing):
        catalog.activate("ref-1")


@pytest.mark.asyncio
async def test_eleven_stream_is_bounded(tmp_path) -> None:
    http = StreamingHttp([b"RIFF", b"\x24\x00\x00\x00WAVEfmt ", b"\x00" * 512])
    adapter = ElevenLabsTtsAdapter(
        project_id="project-1",
        provider_profile_id="profile-1",
        cloud_guard=GuardSpy(),
        http_client=http,
    )

    await adapter.synthesize(_request(), tmp_path / "x.wav")

    assert http.was_streamed
    assert http.max_buffered_bytes <= 1024 * 1024
    assert (tmp_path / "x.wav").read_bytes().startswith(b"RIFF")


class GuardSpy:
    def evaluate(self, **kwargs):
        return CloudCallDecision(
            allowed=True,
            cloud_consent_id="consent-1",
            authorization_id="auth-1",
            rate_card_ids=("rate-1",),
            remaining_quota=(),
            reasons=(),
        )


class StreamingHttp:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.was_streamed = False
        self.max_buffered_bytes = 0

    async def stream(self, method: str, url: str, *, json: dict[str, object], headers: dict[str, str], timeout: int):
        self.was_streamed = True
        return StreamResponse(self)


class StreamResponse:
    status_code = 200
    headers = {"x-request-id": "eleven-request-1"}

    def __init__(self, http: StreamingHttp) -> None:
        self.http = http

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_bytes(self):
        for chunk in self.http.chunks:
            self.http.max_buffered_bytes = max(self.http.max_buffered_bytes, len(chunk))
            yield chunk


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
        voice_id="eleven-voice",
        speed="1.0",
        pitch="0",
        style=None,
        sample_rate=44_100,
    )
