from __future__ import annotations

import asyncio
import hashlib
import io
import wave
from pathlib import Path

import pytest

from app.contracts import OperationContext, SynthesisRequest
from app.modules.voices.catalog import (
    ModelLicenseUnverified,
    VoiceCatalog,
    VoicePreset,
    preview_cache_key,
)
from app.providers.piper import PiperTtsAdapter
from app.providers.vieneu import VieNeuTtsAdapter


def test_unverified_model_license_cannot_activate(tmp_path: Path) -> None:
    catalog = VoiceCatalog(
        presets=(
            VoicePreset(
                id="vieneu-proof",
                name="VieNeu Proof",
                provider="vieneu",
                model="vieneu-vi-int8",
                locale="vi-VN",
                region="local",
                gender="neutral",
                license="model license pending",
                cost_tier="local",
                sample_rate=44_100,
                model_path=tmp_path / "vieneu.onnx",
                license_snapshot_path=tmp_path / "license.txt",
                model_sha256="0" * 64,
                license_snapshot_sha256="1" * 64,
                model_verified=False,
                license_verified=True,
                poc_passed=True,
            ),
        ),
    )

    with pytest.raises(ModelLicenseUnverified):
        catalog.activate("vieneu-proof")


@pytest.mark.asyncio
async def test_piper_uses_argument_list_not_shell(tmp_path: Path) -> None:
    binary = tmp_path / "piper.exe"
    model = tmp_path / "vais1000.onnx"
    config = tmp_path / "vais1000.json"
    binary.write_bytes(b"fake")
    model.write_bytes(b"fake-model")
    config.write_text("{}", encoding="utf-8")
    request = _request(sample_rate=22_050)
    spy = ProcessSpy(_wav_bytes(sample_rate=22_050, duration_seconds=1.0))
    adapter = PiperTtsAdapter(
        binary_path=binary,
        model_path=model,
        config_path=config,
        model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
        process_factory=spy.create_subprocess_exec,
    )

    await adapter.synthesize(request, tmp_path / "voice.wav")

    assert spy.kwargs["shell"] is False
    assert isinstance(spy.argv, list)
    assert spy.argv[0].endswith("piper.exe")
    assert "--model" in spy.argv


@pytest.mark.asyncio
async def test_piper_writes_partial_then_commits_validated_wav(tmp_path: Path) -> None:
    model = tmp_path / "vais1000.onnx"
    model.write_bytes(b"fake-model")
    output_path = tmp_path / "preview.wav"
    adapter = PiperTtsAdapter(
        binary_path=tmp_path / "piper.exe",
        model_path=model,
        config_path=tmp_path / "vais1000.json",
        model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
        process_factory=ProcessSpy(_wav_bytes(sample_rate=22_050, duration_seconds=1.0)).create_subprocess_exec,
    )

    result = await adapter.synthesize(_request(sample_rate=22_050), output_path)

    assert output_path.is_file()
    assert not output_path.with_suffix(".wav.partial").exists()
    assert result.duration_ms == 1000
    assert result.provider == "piper"
    with wave.open(str(output_path), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getnframes()) == (22_050, 1, 22_050)


@pytest.mark.asyncio
async def test_vieneu_rejects_invalid_wav_and_leaves_no_committed_output(tmp_path: Path) -> None:
    model = tmp_path / "vieneu.onnx"
    model.write_bytes(b"fake-vieneu-model")
    output_path = tmp_path / "bad.wav"
    adapter = VieNeuTtsAdapter(
        binary_path=tmp_path / "vieneu.exe",
        model_path=model,
        model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
        process_factory=ProcessSpy(_wav_bytes(sample_rate=44_100, duration_seconds=0.25)).create_subprocess_exec,
    )

    with pytest.raises(ValueError, match="duration"):
        await adapter.synthesize(_request(sample_rate=44_100, voice_id="vieneu-vi-int8"), output_path)

    assert not output_path.exists()
    assert not output_path.with_suffix(".wav.partial").exists()


@pytest.mark.asyncio
async def test_adapter_timeout_terminates_then_kills_process(tmp_path: Path) -> None:
    model = tmp_path / "vais1000.onnx"
    model.write_bytes(b"fake-model")
    process = HangingProcess()
    adapter = PiperTtsAdapter(
        binary_path=tmp_path / "piper.exe",
        model_path=model,
        config_path=tmp_path / "vais1000.json",
        model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
        process_factory=process.create_subprocess_exec,
    )

    with pytest.raises(TimeoutError):
        await adapter.synthesize(_request(timeout_seconds=1, sample_rate=22_050), tmp_path / "timeout.wav")

    assert process.terminated
    assert process.killed


def test_verified_catalog_prefers_vieneu_after_poc_and_piper_fallback(tmp_path: Path) -> None:
    vieneu_model = tmp_path / "vieneu.onnx"
    vieneu_license = tmp_path / "vieneu-license.txt"
    piper_model = tmp_path / "vais1000.onnx"
    piper_license = tmp_path / "piper-license.txt"
    for path, content in (
        (vieneu_model, b"vieneu"),
        (vieneu_license, b"vieneu license"),
        (piper_model, b"piper"),
        (piper_license, b"cc-by-4.0"),
    ):
        path.write_bytes(content)
    catalog = VoiceCatalog(
        presets=(
            VoicePreset(
                id="vieneu-vi-int8",
                name="VieNeu Vietnamese",
                provider="vieneu",
                model="vieneu-vi-int8",
                locale="vi-VN",
                region="local",
                gender="neutral",
                license="verified local model",
                cost_tier="local",
                sample_rate=44_100,
                model_path=vieneu_model,
                license_snapshot_path=vieneu_license,
                model_sha256=hashlib.sha256(vieneu_model.read_bytes()).hexdigest(),
                license_snapshot_sha256=hashlib.sha256(vieneu_license.read_bytes()).hexdigest(),
                model_verified=True,
                license_verified=True,
                poc_passed=True,
            ),
            VoicePreset(
                id="piper-vais1000",
                name="Piper vais1000",
                provider="piper",
                model="vais1000",
                locale="vi-VN",
                region="local",
                gender="neutral",
                license="CC-BY-4.0",
                cost_tier="local",
                sample_rate=22_050,
                model_path=piper_model,
                license_snapshot_path=piper_license,
                model_sha256=hashlib.sha256(piper_model.read_bytes()).hexdigest(),
                license_snapshot_sha256=hashlib.sha256(piper_license.read_bytes()).hexdigest(),
                model_verified=True,
                license_verified=True,
                poc_passed=True,
            ),
        ),
    )

    voices = catalog.list()

    assert [voice.id for voice in voices] == ["vieneu-vi-int8", "piper-vais1000"]
    assert catalog.activate_default().id == "vieneu-vi-int8"
    assert all(not voice.online for voice in voices)
    assert voices[0].favorite is True


def test_catalog_marks_piper_active_when_vieneu_is_not_available(tmp_path: Path) -> None:
    piper_model = tmp_path / "vais1000.onnx"
    piper_license = tmp_path / "piper-license.txt"
    piper_model.write_bytes(b"piper")
    piper_license.write_bytes(b"cc-by-4.0")
    catalog = VoiceCatalog(
        presets=(
            VoicePreset(
                id="vieneu-vi-int8",
                name="VieNeu Vietnamese",
                provider="vieneu",
                model="vieneu-vi-int8",
                locale="vi-VN",
                region="local",
                gender="neutral",
                license="verified local model",
                cost_tier="local",
                sample_rate=44_100,
                model_path=tmp_path / "missing-vieneu.onnx",
                license_snapshot_path=tmp_path / "vieneu-license.txt",
                model_sha256="0" * 64,
                license_snapshot_sha256="1" * 64,
                model_verified=False,
                license_verified=False,
                poc_passed=False,
            ),
            VoicePreset(
                id="piper-vais1000",
                name="Piper vais1000",
                provider="piper",
                model="vais1000",
                locale="vi-VN",
                region="local",
                gender="neutral",
                license="CC-BY-4.0",
                cost_tier="local",
                sample_rate=22_050,
                model_path=piper_model,
                license_snapshot_path=piper_license,
                model_sha256=hashlib.sha256(piper_model.read_bytes()).hexdigest(),
                license_snapshot_sha256=hashlib.sha256(piper_license.read_bytes()).hexdigest(),
                model_verified=True,
                license_verified=True,
                poc_passed=True,
            ),
        ),
    )

    voices = catalog.list()

    assert [(voice.id, voice.active) for voice in voices] == [
        ("vieneu-vi-int8", False),
        ("piper-vais1000", True),
    ]


def test_catalog_guides_missing_model_without_cloud_fallback(tmp_path: Path) -> None:
    catalog = VoiceCatalog(
        presets=(
            VoicePreset(
                id="piper-vais1000",
                name="Piper vais1000",
                provider="piper",
                model="vais1000",
                locale="vi-VN",
                region="local",
                gender="neutral",
                license="CC-BY-4.0",
                cost_tier="local",
                sample_rate=22_050,
                model_path=tmp_path / "missing.onnx",
                license_snapshot_path=tmp_path / "license.txt",
                model_sha256="0" * 64,
                license_snapshot_sha256="1" * 64,
                model_verified=True,
                license_verified=True,
                poc_passed=True,
            ),
        ),
    )

    voice = catalog.list()[0]

    assert voice.available is False
    assert "Install local model" in voice.activation_hint
    assert voice.provider == "piper"
    assert voice.online is False


def test_preview_cache_key_includes_text_preset_settings_model_and_pronunciation() -> None:
    base = preview_cache_key(
        text="Xin chao ban doc.",
        preset_id="piper-vais1000",
        settings_hash="speed=1",
        model_hash="a" * 64,
        pronunciation_hash="names=v1",
    )

    assert base != preview_cache_key(
        text="Xin chao.",
        preset_id="piper-vais1000",
        settings_hash="speed=1",
        model_hash="a" * 64,
        pronunciation_hash="names=v1",
    )
    assert base != preview_cache_key(
        text="Xin chao ban doc.",
        preset_id="piper-vais1000",
        settings_hash="speed=0.95",
        model_hash="a" * 64,
        pronunciation_hash="names=v1",
    )
    assert base != preview_cache_key(
        text="Xin chao ban doc.",
        preset_id="piper-vais1000",
        settings_hash="speed=1",
        model_hash="b" * 64,
        pronunciation_hash="names=v1",
    )
    assert base != preview_cache_key(
        text="Xin chao ban doc.",
        preset_id="piper-vais1000",
        settings_hash="speed=1",
        model_hash="a" * 64,
        pronunciation_hash="names=v2",
    )


def _request(
    *,
    timeout_seconds: int = 30,
    sample_rate: int = 44_100,
    voice_id: str = "piper-vais1000",
) -> SynthesisRequest:
    return SynthesisRequest(
        context=OperationContext(
            operation_id="preview-1",
            cache_key="cache-1",
            timeout_seconds=timeout_seconds,
            estimated_units=15,
            budget_authorization_id=None,
            cloud_consent_id=None,
        ),
        speech_segment_id="speech-1",
        narration_text="Xin chao ban doc cua truyen audio.",
        locale="vi-VN",
        voice_id=voice_id,
        speed="1.0",
        pitch="0",
        style=None,
        sample_rate=sample_rate,
    )


def _wav_bytes(*, sample_rate: int, duration_seconds: float) -> bytes:
    buffer = io.BytesIO()
    frame_count = round(sample_rate * duration_seconds)
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x01" * frame_count)
    return buffer.getvalue()


class ProcessSpy:
    def __init__(self, stdout: bytes) -> None:
        self.stdout = stdout
        self.argv: list[str] = []
        self.kwargs: dict[str, object] = {}

    async def create_subprocess_exec(self, *argv: str, **kwargs: object) -> object:
        self.argv = list(argv)
        self.kwargs = kwargs
        return CompletedProcess(stdout=self.stdout)


class CompletedProcess:
    def __init__(self, stdout: bytes) -> None:
        self.stdout = stdout
        self.stderr = b""
        self.returncode = 0
        self.terminated = False
        self.killed = False

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        return self.stdout, self.stderr

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


class HangingProcess:
    returncode = None

    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    async def create_subprocess_exec(self, *argv: str, **kwargs: object) -> object:
        return self

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        await asyncio.sleep(60)
        return b"", b""

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return 0
