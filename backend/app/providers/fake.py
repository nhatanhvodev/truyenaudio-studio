from __future__ import annotations

import hashlib
import math
import wave
from pathlib import Path

from app.contracts import (
    MasterRequest,
    MasterResult,
    QaCategory,
    QaSeverity,
    ReviewFinding,
    ReviewRequest,
    ReviewResult,
    SynthesisRequest,
    SynthesisResult,
    TranslationRequest,
    TranslationResult,
    Usage,
    UsageUnit,
)


PROVIDER = "fake"
PROVIDER_VERSION = "1"
SAMPLE_RATE = 44_100
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2
SAMPLE_WIDTH_BITS = SAMPLE_WIDTH_BYTES * 8
TTS_DURATION_SECONDS = 1
TTS_FREQUENCY_HZ = 440


class FakeTranslator:
    def capabilities(self) -> dict[str, object]:
        return {
            "provider": PROVIDER,
            "model": "fake-translator",
            "source_languages": ["zh-CN", "vi-VN", "en"],
            "target_languages": ["vi-VN"],
            "network": False,
        }

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        return TranslationResult(
            target_text=f"[VI] {request.source_text}",
            provider=PROVIDER,
            model="fake-translator",
            provider_version=PROVIDER_VERSION,
            usage=(Usage(UsageUnit.CHARACTER.value, len(request.source_text)),),
        )


class FakeReviewer:
    async def review(self, request: ReviewRequest) -> ReviewResult:
        findings: tuple[ReviewFinding, ...] = ()
        residual = _first_han_character(request.target_text)
        if residual is not None:
            findings = (
                ReviewFinding(
                    source_segment_id=request.source_segment_id,
                    category=QaCategory.RESIDUAL_HAN.value,
                    severity=QaSeverity.MAJOR.value,
                    evidence=residual,
                    suggestion="Remove residual Han text from the Vietnamese translation.",
                ),
            )
        return ReviewResult(
            findings=findings,
            provider=PROVIDER,
            model="fake-reviewer",
            provider_version=PROVIDER_VERSION,
            usage=(Usage(UsageUnit.CHARACTER.value, len(request.source_text) + len(request.target_text)),),
        )


class FakeTts:
    def capabilities(self) -> dict[str, object]:
        return {
            "provider": PROVIDER,
            "model": "fake-tts",
            "sample_rates": [SAMPLE_RATE],
            "formats": ["wav"],
            "network": False,
        }

    async def list_voices(self, locale: str) -> list[dict[str, object]]:
        voices = [
            {
                "id": "fake-vi-narrator",
                "name": "Fake Vietnamese Narrator",
                "locale": "vi-VN",
                "sample_rate": SAMPLE_RATE,
            }
        ]
        return [voice for voice in voices if voice["locale"] == locale]

    async def synthesize(self, request: SynthesisRequest, output_path: Path) -> SynthesisResult:
        if request.sample_rate != SAMPLE_RATE:
            raise ValueError("FakeTts supports only locked 44100 Hz sample_rate")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        frames = _sine_pcm_frames(SAMPLE_RATE * TTS_DURATION_SECONDS)
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(CHANNELS)
            wav.setsampwidth(SAMPLE_WIDTH_BYTES)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(frames)

        digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return SynthesisResult(
            provider=PROVIDER,
            model="fake-tts",
            provider_version=PROVIDER_VERSION,
            duration_ms=TTS_DURATION_SECONDS * 1000,
            sha256=digest,
            usage=(Usage(UsageUnit.AUDIO_SECOND.value, TTS_DURATION_SECONDS),),
        )


class FakeAudioProcessor:
    async def master(self, request: MasterRequest, output_path: Path) -> MasterResult:
        if request.sample_rate != SAMPLE_RATE:
            raise ValueError("FakeAudioProcessor supports only locked 44100 Hz sample_rate")
        if len(request.ordered_segment_paths) != len(request.pause_after_ms):
            raise ValueError("pause_after_ms must match ordered_segment_paths length")
        if any(not isinstance(pause_ms, int) or isinstance(pause_ms, bool) for pause_ms in request.pause_after_ms):
            raise ValueError("pause_after_ms values must be integer milliseconds")
        if any(pause_ms < 0 for pause_ms in request.pause_after_ms):
            raise ValueError("pause_after_ms values must be non-negative")
        if any((SAMPLE_RATE * pause_ms) % 1000 != 0 for pause_ms in request.pause_after_ms):
            raise ValueError("pause_after_ms values must be aligned exactly to whole PCM frames")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(CHANNELS)
            output.setsampwidth(SAMPLE_WIDTH_BYTES)
            output.setframerate(SAMPLE_RATE)
            for segment_path, pause_ms in zip(request.ordered_segment_paths, request.pause_after_ms, strict=True):
                with wave.open(str(segment_path), "rb") as segment:
                    _validate_wav(segment)
                    while True:
                        chunk = segment.readframes(4096)
                        if not chunk:
                            break
                        output.writeframesraw(chunk)
                pause_frames = round(SAMPLE_RATE * pause_ms / 1000)
                if pause_frames:
                    output.writeframesraw(b"\x00" * pause_frames * CHANNELS * SAMPLE_WIDTH_BYTES)

        return _probe_wav(output_path, request.integrated_lufs, request.true_peak_dbtp)

    async def probe(self, path: Path) -> MasterResult:
        return _probe_wav(Path(path), integrated_lufs=-16.0, true_peak_dbtp=-1.5)


class FakeMp3AudioProcessor:
    async def master(self, request: MasterRequest, output_path: Path) -> MasterResult:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        for path in request.ordered_segment_paths:
            digest.update(Path(path).read_bytes())
        digest.update(",".join(str(pause) for pause in request.pause_after_ms).encode("ascii"))
        payload = b"FAKE_MP3\n" + digest.hexdigest().encode("ascii") + b"\n"
        output_path.write_bytes(payload)
        return await self.probe(output_path)

    async def probe(self, path: Path, expected_sha256: str | None = None) -> MasterResult:
        actual_sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise ValueError("master checksum mismatch")
        return MasterResult(
            duration_ms=30_000,
            sha256=actual_sha256,
            codec="mp3",
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
            bitrate_kbps=128,
            integrated_lufs=-16.0,
            true_peak_dbtp=-1.5,
        )


def _first_han_character(value: str) -> str | None:
    for character in value:
        if "\u4e00" <= character <= "\u9fff":
            return character
    return None


def _sine_pcm_frames(frame_count: int) -> bytes:
    frames = bytearray()
    amplitude = 16_384
    for index in range(frame_count):
        sample = round(amplitude * math.sin(2 * math.pi * TTS_FREQUENCY_HZ * index / SAMPLE_RATE))
        frames.extend(int(sample).to_bytes(SAMPLE_WIDTH_BYTES, byteorder="little", signed=True))
    return bytes(frames)


def _validate_wav(wav: wave.Wave_read) -> None:
    actual = (wav.getframerate(), wav.getnchannels(), wav.getsampwidth())
    expected = (SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH_BYTES)
    if actual != expected:
        raise ValueError(f"incompatible WAV format: expected {expected}, got {actual}")


def _probe_wav(path: Path, integrated_lufs: float, true_peak_dbtp: float) -> MasterResult:
    with wave.open(str(path), "rb") as wav:
        _validate_wav(wav)
        frame_count = wav.getnframes()
    duration_ms = round(frame_count * 1000 / SAMPLE_RATE)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    bitrate_kbps = round(SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH_BITS / 1000)
    return MasterResult(
        duration_ms=duration_ms,
        sha256=digest,
        codec="pcm_s16le",
        sample_rate=SAMPLE_RATE,
        channels=CHANNELS,
        bitrate_kbps=bitrate_kbps,
        integrated_lufs=integrated_lufs,
        true_peak_dbtp=true_peak_dbtp,
    )
