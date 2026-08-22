from __future__ import annotations

from app.contracts import AsrRequest, AsrResult, Usage, UsageUnit


class FakeAsrAdapter:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript

    async def transcribe(self, request: AsrRequest) -> AsrResult:
        return AsrResult(
            transcript=self.transcript,
            provider="fake",
            model="fake-asr",
            provider_version="1",
            usage=(Usage(UsageUnit.AUDIO_SECOND.value, 0),),
        )
