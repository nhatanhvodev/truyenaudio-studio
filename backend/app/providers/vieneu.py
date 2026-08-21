from __future__ import annotations

import asyncio
from asyncio import subprocess
import hashlib
import os
from pathlib import Path
import wave
from typing import Awaitable, Callable

from app.contracts import SynthesisRequest, SynthesisResult, Usage, UsageUnit


MIN_DURATION_SECONDS = 0.5
MAX_DURATION_SECONDS = 120.0
SAMPLE_WIDTH_BYTES = 2

ProcessFactory = Callable[..., Awaitable[object]]


class LocalProcessTtsAdapter:
    provider = "local"
    provider_version = "local-subprocess"
    default_sample_rate = 44_100

    def __init__(
        self,
        *,
        binary_path: Path,
        model_path: Path,
        model_sha256: str,
        process_factory: ProcessFactory | None = None,
    ) -> None:
        self.binary_path = Path(binary_path)
        self.model_path = Path(model_path)
        self.model_sha256 = model_sha256
        self._process_factory = process_factory or asyncio.create_subprocess_exec

    async def synthesize(self, request: SynthesisRequest, output_path: Path) -> SynthesisResult:
        self._validate_request(request)
        self._verify_model_snapshot()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path = output_path.with_suffix(f"{output_path.suffix}.partial")
        partial_path.unlink(missing_ok=True)

        process = await self._process_factory(*self._argv(request, partial_path), **self._kwargs())
        try:
            stdout, stderr = await self._communicate(process, request)
            returncode = getattr(process, "returncode", 0)
            if returncode not in (0, None):
                raise RuntimeError(f"{self.provider} failed: {_redacted_streams(stdout, stderr)}")
            if stdout and not partial_path.exists():
                partial_path.write_bytes(stdout)
            duration_ms, sha256 = _validate_wav(partial_path, request.sample_rate)
            os.replace(partial_path, output_path)
            return self._result(request, output_path, duration_ms, sha256)
        except BaseException:
            partial_path.unlink(missing_ok=True)
            raise

    def _validate_request(self, request: SynthesisRequest) -> None:
        if request.locale != "vi-VN":
            raise ValueError("local Vietnamese TTS adapters support only vi-VN")
        if request.sample_rate != self.default_sample_rate:
            raise ValueError(f"{self.provider} supports only locked {self.default_sample_rate} Hz sample_rate")
        if not request.narration_text.strip():
            raise ValueError("narration_text is required")

    def _verify_model_snapshot(self) -> None:
        if not self.model_path.is_file():
            raise FileNotFoundError("Install local model snapshot before synthesis.")
        actual = hashlib.sha256(self.model_path.read_bytes()).hexdigest()
        if actual != self.model_sha256:
            raise ValueError("local model snapshot hash mismatch")

    async def _communicate(self, process: object, request: SynthesisRequest) -> tuple[bytes, bytes]:
        communicate = process.communicate(input=request.narration_text.encode("utf-8"))
        try:
            return await asyncio.wait_for(communicate, timeout=request.context.timeout_seconds)
        except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError):
            await _terminate_then_kill(process)
            raise

    def _kwargs(self) -> dict[str, object]:
        return {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
        }

    def _argv(self, request: SynthesisRequest, partial_path: Path) -> list[str]:
        raise NotImplementedError

    def _model_name(self) -> str:
        raise NotImplementedError

    def _result(self, request: SynthesisRequest, output_path: Path, duration_ms: int, sha256: str) -> SynthesisResult:
        raise NotImplementedError


class VieNeuTtsAdapter(LocalProcessTtsAdapter):
    provider = "vieneu"
    provider_version = "local-onnx-int8"
    default_sample_rate = 44_100

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": "vieneu-vi-int8",
            "engine": "onnx-int8",
            "sample_rates": [self.default_sample_rate],
            "formats": ["wav"],
            "network": False,
        }

    async def list_voices(self, locale: str) -> list[dict[str, object]]:
        if locale != "vi-VN":
            return []
        return [
            {
                "id": "vieneu-vi-int8",
                "name": "VieNeu Vietnamese",
                "locale": "vi-VN",
                "sample_rate": self.default_sample_rate,
                "engine": "onnx-int8",
            }
        ]

    def _model_name(self) -> str:
        return "vieneu-vi-int8"

    def _argv(self, request: SynthesisRequest, partial_path: Path) -> list[str]:
        if request.voice_id != "vieneu-vi-int8":
            raise ValueError("VieNeu local adapter supports only vieneu-vi-int8")
        return [
            str(self.binary_path),
            "--model",
            str(self.model_path),
            "--sample-rate",
            str(request.sample_rate),
            "--output",
            str(partial_path),
        ]

    def _result(self, request: SynthesisRequest, output_path: Path, duration_ms: int, sha256: str) -> SynthesisResult:
        return SynthesisResult(
            provider=self.provider,
            model=self._model_name(),
            provider_version=self.provider_version,
            duration_ms=duration_ms,
            sha256=sha256,
            usage=(Usage(UsageUnit.AUDIO_SECOND.value, max(1, round(duration_ms / 1000))),),
        )


async def _terminate_then_kill(process: object) -> None:
    terminate = getattr(process, "terminate", None)
    if callable(terminate):
        terminate()
    wait = getattr(process, "wait", None)
    if callable(wait):
        try:
            await asyncio.wait_for(wait(), timeout=0.2)
        except (TimeoutError, asyncio.TimeoutError):
            pass
    if getattr(process, "returncode", None) is None:
        kill = getattr(process, "kill", None)
        if callable(kill):
            kill()
        if callable(wait):
            await wait()


def _validate_wav(path: Path, expected_sample_rate: int) -> tuple[int, str]:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("WAV output is empty")
    with path.open("rb") as file:
        if file.read(4) != b"RIFF":
            raise ValueError("WAV output missing RIFF header")
    with wave.open(str(path), "rb") as wav:
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        frame_count = wav.getnframes()
        sample_width = wav.getsampwidth()
    if channels != 1:
        raise ValueError("WAV output must be mono")
    if sample_rate != expected_sample_rate:
        raise ValueError("WAV output sample_rate mismatch")
    if sample_width != SAMPLE_WIDTH_BYTES:
        raise ValueError("WAV output must be signed 16-bit PCM")
    if frame_count <= 0:
        raise ValueError("WAV output has no audio frames")
    duration_seconds = frame_count / sample_rate
    if duration_seconds < MIN_DURATION_SECONDS or duration_seconds > MAX_DURATION_SECONDS:
        raise ValueError("WAV output duration outside 0.5-120 seconds")
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return round(duration_seconds * 1000), sha256


def _redacted_streams(stdout: bytes, stderr: bytes) -> str:
    return f"stdout={len(stdout)} bytes, stderr={len(stderr)} bytes"
