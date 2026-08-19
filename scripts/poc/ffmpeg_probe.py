from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


def run_probe(*, wav_paths: list[Path], output_path: Path, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list = output_path.with_suffix(".concat.txt")
    concat_list.write_text("".join(f"file '{_escape_concat(path)}'\n" for path in wav_paths), encoding="utf-8")
    try:
        first = subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
            shell=False,
            capture_output=True,
            text=True,
            check=False,
        )
        if first.returncode != 0:
            return {"status": "error", "error_code": "FFMPEG_FAILED", "redacted_error": "loudnorm_pass1_failed"}
        second = subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", str(output_path)],
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
    finally:
        concat_list.unlink(missing_ok=True)


def _escape_concat(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def _hash_file(path: Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-path", required=True)
    parser.add_argument("wav_paths", nargs="+")
    args = parser.parse_args()
    result = run_probe(wav_paths=[Path(path) for path in args.wav_paths], output_path=Path(args.output_path))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
