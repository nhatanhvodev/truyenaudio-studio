from __future__ import annotations

import copy
import hashlib
import socket
import wave
from pathlib import Path

import pytest

from app.contracts import (
    MasterRequest,
    QaCategory,
    QaSeverity,
    ReviewRequest,
    SynthesisRequest,
    TranslationRequest,
    Usage,
    UsageUnit,
)


EXPECTED_WAV_SHA256 = "6e5107c654012e75fc0407ebc524cedad31b84249f9543db1064e4f42b06c742"


def install_socket_tripwire(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    calls: list[object] = []

    def fail_connect(self: socket.socket, address: object) -> None:
        calls.append(address)
        raise AssertionError(f"adapter attempted network access: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", fail_connect)
    return calls


async def assert_translator_contract(adapter, request: TranslationRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    network_calls = install_socket_tripwire(monkeypatch)
    before = copy.deepcopy(request)

    first = await adapter.translate(request)
    second = await adapter.translate(request)

    assert request == before
    assert first == second
    assert first.target_text == f"[VI] {request.source_text}"
    assert first.provider == "fake"
    assert first.model == "fake-translator"
    assert first.provider_version == "1"
    assert first.usage == (Usage(UsageUnit.CHARACTER.value, len(request.source_text)),)
    assert network_calls == []


async def assert_reviewer_contract(adapter, clean_request: ReviewRequest, han_request: ReviewRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    network_calls = install_socket_tripwire(monkeypatch)
    before = copy.deepcopy(han_request)

    clean_first = await adapter.review(clean_request)
    clean_second = await adapter.review(clean_request)
    han_first = await adapter.review(han_request)
    han_second = await adapter.review(han_request)

    assert han_request == before
    assert clean_first == clean_second
    assert clean_first.findings == ()
    assert han_first == han_second
    assert len(han_first.findings) == 1
    finding = han_first.findings[0]
    assert finding.source_segment_id == han_request.source_segment_id
    assert finding.category == QaCategory.RESIDUAL_HAN.value
    assert finding.severity == QaSeverity.MAJOR.value
    assert "漢" in finding.evidence
    assert han_first.provider == "fake"
    assert han_first.model == "fake-reviewer"
    assert han_first.provider_version == "1"
    assert han_first.usage == (
        Usage(UsageUnit.CHARACTER.value, len(han_request.source_text) + len(han_request.target_text)),
    )
    assert network_calls == []


async def assert_tts_contract(adapter, request: SynthesisRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    network_calls = install_socket_tripwire(monkeypatch)
    before = copy.deepcopy(request)
    output_path = tmp_path / "nested" / "voice.wav"

    first = await adapter.synthesize(request, output_path)
    first_bytes = output_path.read_bytes()
    second = await adapter.synthesize(request, output_path)
    second_bytes = output_path.read_bytes()

    assert request == before
    assert first == second
    assert first_bytes == second_bytes
    assert first.duration_ms == 1000
    assert first.sha256 == hashlib.sha256(first_bytes).hexdigest()
    assert first.sha256 == EXPECTED_WAV_SHA256
    assert first.provider == "fake"
    assert first.model == "fake-tts"
    assert first.provider_version == "1"
    assert first.usage == (Usage(UsageUnit.AUDIO_SECOND.value, 1),)
    with wave.open(str(output_path), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (44_100, 1, 2)
        assert wav.getnframes() == 44_100
    assert adapter.capabilities() == adapter.capabilities()
    assert await adapter.list_voices("vi-VN") == await adapter.list_voices("vi-VN")
    assert network_calls == []

    invalid = SynthesisRequest(
        context=request.context,
        speech_segment_id=request.speech_segment_id,
        narration_text=request.narration_text,
        locale=request.locale,
        voice_id=request.voice_id,
        speed=request.speed,
        pitch=request.pitch,
        style=request.style,
        sample_rate=48_000,
    )
    with pytest.raises(ValueError, match="44100"):
        await adapter.synthesize(invalid, tmp_path / "invalid.wav")


async def assert_audio_processor_contract(adapter, tts_adapter, request: SynthesisRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    network_calls = install_socket_tripwire(monkeypatch)
    segment_a = tmp_path / "a.wav"
    segment_b = tmp_path / "b.wav"
    await tts_adapter.synthesize(request, segment_a)
    await tts_adapter.synthesize(request, segment_b)
    master_path = tmp_path / "masters" / "joined.wav"
    master_request = MasterRequest(
        operation_id="master-op",
        ordered_segment_paths=(segment_a, segment_b),
        pause_after_ms=(250, 0),
        metadata={"title": "Fake Master"},
    )
    before = copy.deepcopy(master_request)

    first = await adapter.master(master_request, master_path)
    first_bytes = master_path.read_bytes()
    second = await adapter.master(master_request, master_path)
    second_bytes = master_path.read_bytes()
    probed = await adapter.probe(master_path)

    assert master_request == before
    assert first == second == probed
    assert first_bytes == second_bytes
    assert first.duration_ms == 2250
    assert first.sha256 == hashlib.sha256(first_bytes).hexdigest()
    assert first.codec == "pcm_s16le"
    assert first.sample_rate == 44_100
    assert first.channels == 1
    assert first.bitrate_kbps == 706
    with wave.open(str(master_path), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (44_100, 1, 2)
        assert wav.getnframes() == 99_225
    assert network_calls == []

    with pytest.raises(ValueError, match="pause"):
        await adapter.master(
            MasterRequest(
                operation_id="bad-pause",
                ordered_segment_paths=(segment_a, segment_b),
                pause_after_ms=(0,),
                metadata={},
            ),
            tmp_path / "bad-pause.wav",
        )
    misaligned_output = tmp_path / "misaligned.wav"
    with pytest.raises(ValueError, match="aligned"):
        await adapter.master(
            MasterRequest(
                operation_id="misaligned-pause",
                ordered_segment_paths=(segment_a,),
                pause_after_ms=(1,),
                metadata={},
            ),
            misaligned_output,
        )
    assert not misaligned_output.exists()
