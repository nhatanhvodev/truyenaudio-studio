from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable

from app.contracts import MasterRequest, MasterResult


Runner = Callable[[list[str]], Any]
LOUDNORM_TARGET = "I=-16:TP=-1.5:LRA=11"


class FFmpegAudioProcessor:
    def __init__(
        self,
        *,
        ffmpeg_binary: str = "ffmpeg",
        ffprobe_binary: str = "ffprobe",
        runner: Callable[..., Any] | None = None,
    ) -> None:
        self.ffmpeg_binary = ffmpeg_binary
        self.ffprobe_binary = ffprobe_binary
        self._runner = runner or subprocess.run

    async def master(self, request: MasterRequest, output_path: Path) -> MasterResult:
        if not request.ordered_segment_paths:
            raise ValueError("ordered_segment_paths is required")
        if len(request.ordered_segment_paths) != len(request.pause_after_ms):
            raise ValueError("pause_after_ms must match ordered_segment_paths")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        concat_path = output_path.with_suffix(".concat.txt")
        partial_path = output_path.with_suffix(f"{output_path.suffix}.partial")
        partial_path.unlink(missing_ok=True)
        concat_inputs = self._concat_inputs(request, output_path.parent)
        concat_path.write_text(_concat_list(concat_inputs), encoding="utf-8")

        pass1 = [
            self.ffmpeg_binary,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-af",
            f"aresample={request.sample_rate},aformat=channel_layouts=mono,"
            f"loudnorm={LOUDNORM_TARGET}:print_format=json",
            "-f",
            "null",
            _null_sink(),
        ]
        completed = self._run(pass1)
        metrics = _parse_loudnorm_json(completed.stderr)
        loudnorm_filter = (
            f"aresample={request.sample_rate},aformat=channel_layouts=mono,"
            f"loudnorm={LOUDNORM_TARGET}:"
            f"measured_I={metrics['input_i']}:"
            f"measured_TP={metrics['input_tp']}:"
            f"measured_LRA={metrics['input_lra']}:"
            f"measured_thresh={metrics['input_thresh']}:"
            f"offset={metrics['target_offset']}:linear=true:print_format=summary"
        )
        pass2 = [
            self.ffmpeg_binary,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-af",
            loudnorm_filter,
            "-ar",
            str(request.sample_rate),
            "-ac",
            "1",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-write_id3v2",
            "1",
            "-id3v2_version",
            "3",
        ]
        for key, value in sorted(request.metadata.items()):
            pass2.extend(("-metadata", f"{key}={value}"))
        pass2.append(str(partial_path))
        self._run(pass2)
        os.replace(partial_path, output_path)
        probed = await self.probe(output_path)
        return replace(
            probed,
            integrated_lufs=probed.integrated_lufs,
            true_peak_dbtp=probed.true_peak_dbtp,
        )

    async def probe(self, path: Path, expected_sha256: str | None = None) -> MasterResult:
        path = Path(path)
        actual_sha256 = _sha256_file(path)
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise ValueError("master checksum mismatch")
        ffprobe = [
            self.ffprobe_binary,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,bit_rate:format=duration,bit_rate",
            "-of",
            "json",
            str(path),
        ]
        completed = self._run(ffprobe)
        data = json.loads(completed.stdout)
        stream = data["streams"][0]
        duration_ms = round(float(data["format"]["duration"]) * 1000)
        bitrate = int(stream.get("bit_rate") or data["format"].get("bit_rate") or 0)
        loudness = self._measure_loudness(path)
        return MasterResult(
            duration_ms=duration_ms,
            sha256=actual_sha256,
            codec=str(stream["codec_name"]),
            sample_rate=int(stream["sample_rate"]),
            channels=int(stream["channels"]),
            bitrate_kbps=round(bitrate / 1000),
            integrated_lufs=float(loudness["input_i"]),
            true_peak_dbtp=float(loudness["input_tp"]),
        )

    def _measure_loudness(self, path: Path) -> dict[str, str]:
        argv = [
            self.ffmpeg_binary,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(path),
            "-af",
            f"loudnorm={LOUDNORM_TARGET}:print_format=json",
            "-f",
            "null",
            _null_sink(),
        ]
        return _parse_loudnorm_json(self._run(argv).stderr)

    def _run(self, argv: list[str]) -> Any:
        completed = self._runner(
            argv,
            capture_output=True,
            text=True,
            shell=False,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"{argv[0]} failed: stderr={len(completed.stderr)} bytes")
        return completed

    def _concat_inputs(self, request: MasterRequest, work_root: Path) -> tuple[Path, ...]:
        inputs: list[Path] = []
        for index, (segment_path, pause_ms) in enumerate(
            zip(request.ordered_segment_paths, request.pause_after_ms, strict=True)
        ):
            inputs.append(segment_path)
            if pause_ms <= 0:
                continue
            silence_path = work_root / f"{request.operation_id}.{index}.silence.wav"
            duration_seconds = pause_ms / 1000
            self._run(
                [
                    self.ffmpeg_binary,
                    "-hide_banner",
                    "-nostdin",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"anullsrc=r={request.sample_rate}:cl=mono",
                    "-t",
                    f"{duration_seconds:.3f}",
                    "-acodec",
                    "pcm_s16le",
                    str(silence_path),
                ]
            )
            inputs.append(silence_path)
        return tuple(inputs)


def _concat_list(paths: tuple[Path, ...]) -> str:
    return "".join(f"file '{_ffmpeg_path(path)}'\n" for path in paths)


def _ffmpeg_path(path: Path) -> str:
    return str(Path(path).resolve()).replace("\\", "/").replace("'", "'\\''")


def _parse_loudnorm_json(stderr: str) -> dict[str, str]:
    for candidate in re.findall(r"\{[^{}]*\}", stderr, flags=re.DOTALL):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
        if all(key in data for key in required):
            return {key: str(data[key]) for key in required}
    raise RuntimeError("loudnorm_metrics_invalid")


def _sha256_file(path: Path) -> str:
    sha256 = hashlib.sha256()
    with Path(path).open("rb") as file:
        while chunk := file.read(1024 * 1024):
            sha256.update(chunk)
    return sha256.hexdigest()


def _null_sink() -> str:
    return "NUL" if os.name == "nt" else "/dev/null"
