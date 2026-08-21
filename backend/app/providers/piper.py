from __future__ import annotations

from asyncio import subprocess
from pathlib import Path

from app.contracts import SynthesisRequest, SynthesisResult, Usage, UsageUnit
from app.providers.vieneu import LocalProcessTtsAdapter


class PiperTtsAdapter(LocalProcessTtsAdapter):
    provider = "piper"
    provider_version = "local-subprocess"
    default_sample_rate = 22_050

    def __init__(
        self,
        *,
        binary_path: Path,
        model_path: Path,
        config_path: Path,
        model_sha256: str,
        process_factory=None,
    ) -> None:
        super().__init__(
            binary_path=binary_path,
            model_path=model_path,
            model_sha256=model_sha256,
            process_factory=process_factory,
        )
        self.config_path = Path(config_path)

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": "vais1000",
            "license": "CC-BY-4.0",
            "sample_rates": [self.default_sample_rate],
            "formats": ["wav"],
            "network": False,
        }

    async def list_voices(self, locale: str) -> list[dict[str, object]]:
        if locale != "vi-VN":
            return []
        return [
            {
                "id": "piper-vais1000",
                "name": "Piper vais1000",
                "locale": "vi-VN",
                "sample_rate": self.default_sample_rate,
                "license": "CC-BY-4.0",
            }
        ]

    def _model_name(self) -> str:
        return "vais1000"

    def _argv(self, request: SynthesisRequest, partial_path: Path) -> list[str]:
        if request.voice_id != "piper-vais1000":
            raise ValueError("Piper local adapter supports only piper-vais1000")
        return [
            str(self.binary_path),
            "--model",
            str(self.model_path),
            "--config",
            str(self.config_path),
            "--output_file",
            str(partial_path),
        ]

    def _kwargs(self) -> dict[str, object]:
        return {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
        }

    def _result(self, request: SynthesisRequest, output_path: Path, duration_ms: int, sha256: str) -> SynthesisResult:
        return SynthesisResult(
            provider=self.provider,
            model=self._model_name(),
            provider_version=self.provider_version,
            duration_ms=duration_ms,
            sha256=sha256,
            usage=(Usage(UsageUnit.AUDIO_SECOND.value, max(1, round(duration_ms / 1000))),),
        )
