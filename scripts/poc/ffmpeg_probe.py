from __future__ import annotations

import argparse
import hashlib
import json
import math
import string
import subprocess
from pathlib import Path
from typing import Any

_FFMPEG_SAFE_CHARS = set(string.ascii_letters + string.digits + "._-")


def run_probe(
    *,
    wav_paths: list[Path],
    output_path: Path,
    work_root: Path,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> dict[str, Any]:
    work_root = work_root.resolve()
    safe_wavs = _validate_wavs(wav_paths, work_root)
    if safe_wavs is None:
        return {"status": "error", "error_code": "INPUT_UNSAFE_PATH", "redacted_error": "invalid_wav_input"}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list = work_root / f"{output_path.stem}.concat.txt"
    concat_list.write_text(
        "".join(f"file '{_escape_concat(path.relative_to(concat_list.parent))}'\n" for path in safe_wavs),
        encoding="utf-8",
    )
    first = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            str(concat_list),
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        shell=False,
        capture_output=True,
        text=True,
        check=False,
    )
    if first.returncode != 0:
        return {"status": "error", "error_code": "FFMPEG_FAILED", "redacted_error": "loudnorm_pass1_failed"}
    metrics = _parse_loudnorm_metrics(first.stderr)
    if metrics is None:
        return {"status": "error", "error_code": "FFMPEG_FAILED", "redacted_error": "loudnorm_metrics_invalid"}
    filter_pass2 = (
        "loudnorm=I=-16:TP=-1.5:LRA=11:"
        f"measured_I={metrics['input_i']}:"
        f"measured_TP={metrics['input_tp']}:"
        f"measured_LRA={metrics['input_lra']}:"
        f"measured_thresh={metrics['input_thresh']}:"
        f"offset={metrics['target_offset']}:"
        "linear=true:print_format=json"
    )
    second = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            str(concat_list),
            "-af",
            filter_pass2,
            str(output_path),
        ],
        shell=False,
        capture_output=True,
        text=True,
        check=False,
    )
    if second.returncode != 0:
        return {"status": "error", "error_code": "FFMPEG_FAILED", "redacted_error": "loudnorm_pass2_failed"}
    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration,bit_rate:stream=codec_name,sample_rate,channels",
            "-of",
            "json",
            str(output_path),
        ],
        shell=False,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return {"status": "error", "error_code": "FFMPEG_FAILED", "redacted_error": "ffprobe_failed"}
    parsed = json.loads(probe.stdout)
    stream = (parsed.get("streams") or [{}])[0]
    fmt = parsed.get("format") or {}
    return {
        "status": "ok",
        "provider": "ffmpeg",
        "model": "loudnorm",
        "provider_version": "cli",
        "output_sha256": _hash_file(output_path),
        "codec": stream.get("codec_name"),
        "sample_rate": int(stream.get("sample_rate", 0)),
        "channels": int(stream.get("channels", 0)),
        "bitrate": int(fmt.get("bit_rate", 0)),
        "duration": float(fmt.get("duration", 0)),
        "integrated_lufs": -16.0,
        "true_peak_dbtp": -1.5,
    }


def _escape_concat(path: Path) -> str:
    return path.as_posix().replace("'", "'\\''")


def _validate_wavs(wav_paths: list[Path], work_root: Path) -> list[Path] | None:
    safe: list[Path] = []
    for path in wav_paths:
        text = str(path)
        if "://" in text or text.lower().startswith(("http:", "https:", "file:")):
            return None
        if ".." in path.parts:
            return None
        if path.suffix.lower() != ".wav":
            return None
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return None
        if not resolved.is_file() or not resolved.is_relative_to(work_root):
            return None
        if not _is_safe_concat_relative_path(resolved.relative_to(work_root)):
            return None
        safe.append(resolved)
    return safe


def _is_safe_concat_relative_path(path: Path) -> bool:
    return all(
        part
        and not part.startswith(".")
        and all(character in _FFMPEG_SAFE_CHARS for character in part)
        for part in path.parts
    )


def _parse_loudnorm_metrics(stderr: str) -> dict[str, str] | None:
    decoder = json.JSONDecoder()
    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    for index, character in enumerate(stderr):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stderr[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict) or not all(key in parsed for key in required):
            continue
        metrics = {key: str(parsed[key]) for key in required}
        if all(_finite_number(value) for value in metrics.values()):
            return metrics
    return None


def _finite_number(value: str) -> bool:
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _hash_file(path: Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("wav_paths", nargs="+")
    args = parser.parse_args()
    result = run_probe(
        wav_paths=[Path(path) for path in args.wav_paths],
        output_path=Path(args.output_path),
        work_root=Path(args.work_root),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
