from __future__ import annotations

from pathlib import Path

import pytest

from app.contracts import OperationContext, SynthesisRequest, Usage
from app.modules.compliance.cloud import CloudCallBlocked, CloudCallDecision
from app.providers.google_tts import GoogleTtsAdapter


@pytest.mark.asyncio
async def test_google_requires_guard_before_http(tmp_path: Path) -> None:
    http = HttpSpy()
    adapter = GoogleTtsAdapter(
        project_id="project-1",
        provider_profile_id="profile-1",
        cloud_guard=GuardSpy(allowed=False),
        http_client=http,
    )

    with pytest.raises(CloudCallBlocked):
        await adapter.synthesize(_request(), tmp_path / "x.wav")

    assert http.calls == []


@pytest.mark.asyncio
async def test_google_counts_billable_characters(tmp_path: Path) -> None:
    http = HttpSpy({"audioContent": WAV_B64})
    adapter = GoogleTtsAdapter(
        project_id="project-1",
        provider_profile_id="profile-1",
        cloud_guard=GuardSpy(allowed=True),
        http_client=http,
    )

    result = await adapter.synthesize(_request(), tmp_path / "x.wav")

    assert result.provider == "google"
    assert result.model == "neural2"
    assert result.usage == (Usage("CHARACTER", len(_request().narration_text), "google-request-1"),)
    assert (tmp_path / "x.wav").read_bytes().startswith(b"RIFF")


class GuardSpy:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed

    def evaluate(self, **kwargs):
        if self.allowed:
            return CloudCallDecision(
                allowed=True,
                cloud_consent_id="consent-1",
                authorization_id="auth-1",
                rate_card_ids=("rate-1",),
                remaining_quota=(),
                reasons=(),
            )
        return CloudCallDecision(
            allowed=False,
            cloud_consent_id=None,
            authorization_id=None,
            rate_card_ids=(),
            remaining_quota=(),
            reasons=("CONSENT_NOT_GRANTED",),
        )


class HttpSpy:
    def __init__(self, payload: dict[str, object] | None = None) -> None:
        self.payload = payload or {}
        self.calls: list[dict[str, object]] = []

    async def post(self, url: str, *, json: dict[str, object], headers: dict[str, str], timeout: int):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return ResponseSpy(self.payload)


class ResponseSpy:
    status_code = 200
    headers = {"x-request-id": "google-request-1"}

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


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
        voice_id="vi-VN-Neural2-A",
        speed="1.0",
        pitch="0",
        style=None,
        sample_rate=44_100,
    )


WAV_B64 = "UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA="
