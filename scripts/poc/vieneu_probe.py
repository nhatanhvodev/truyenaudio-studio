from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import subprocess
import time
from pathlib import Path
from typing import Any


VOICE_PRESETS = ("vi-vieneu-1", "vi-vieneu-2", "vi-vieneu-3", "vi-vieneu-4")
REQUIRED_VIENEU_VERSION = "3.2.9"
REQUIRED_PSUTIL_VERSION = "7.0.0"


def run_probe(*, model_path: Path, executable: Path, output_dir: Path, allow_local_model: bool = False) -> dict[str, Any]:
    if not allow_local_model:
        return {
            "status": "blocked",
            "error_code": "POC_LOCAL_MODEL_REQUIRED",
            "redacted_error": "allow_local_model_required",
        }

    if not model_path.is_file():
        return {"status": "blocked", "error_code": "MODEL_MISSING", "redacted_error": "model_missing"}
    if not executable.is_file():
        return {"status": "blocked", "error_code": "MODEL_MISSING", "redacted_error": "executable_missing"}

    version_error = _version_error()
    if version_error is not None:
        return {"status": "blocked", "error_code": "MODEL_MISSING", "redacted_error": version_error}

    try:
        import psutil
        import vieneu
    except ImportError as exc:
        return {"status": "blocked", "error_code": "MODEL_MISSING", "redacted_error": type(exc).__name__}

    psutil_error = getattr(psutil, "Error", OSError)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics = []
    peak_mb = 0.0
    started = time.perf_counter()
    for preset in VOICE_PRESETS:
        output_path = output_dir / f"{preset}.wav"
        try:
            process = subprocess.Popen(
                [str(executable), "--model", str(model_path), "--voice", preset, "--output", str(output_path)],
                shell=False,
            )
        except OSError:
            return {"status": "error", "error_code": "MODEL_FAILED", "redacted_error": "process_start_failed"}
        try:
            root = psutil.Process(process.pid)
            while process.poll() is None:
                rss = root.memory_info().rss + sum(child.memory_info().rss for child in root.children(recursive=True))
                peak_mb = max(peak_mb, rss / 1024 / 1024)
                time.sleep(0.05)
        except (OSError, psutil_error):
            return {
                "status": "error",
                "error_code": "MODEL_FAILED",
                "redacted_error": "process_sampling_failed",
                "metrics": metrics,
            }
        metrics.append({"voice_id": preset, "exit_code": process.returncode})
        if process.returncode != 0:
            return {"status": "error", "error_code": "MODEL_FAILED", "redacted_error": "nonzero_exit", "metrics": metrics}
    elapsed = max(time.perf_counter() - started, 0.001)
    try:
        model_snapshot_hash = _hash_file(model_path)
    except OSError:
        return {
            "status": "error",
            "error_code": "MODEL_FAILED",
            "redacted_error": "model_snapshot_hash_failed",
            "metrics": metrics,
        }
    return {
        "status": "ok",
        "provider": "vieneu",
        "model": "vieneu-tts-v3-turbo",
        "provider_version": getattr(vieneu, "__version__", "unknown"),
        "model_snapshot_hash": model_snapshot_hash,
        "voices": metrics,
        "peak_working_set_mb": round(peak_mb, 2),
        "rtf": round(elapsed / 720, 4),
        "oom": False,
    }


def _hash_file(path: Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _version_error() -> str | None:
    try:
        vieneu_version = metadata.version("vieneu")
    except metadata.PackageNotFoundError:
        return "vieneu_missing"
    if vieneu_version != REQUIRED_VIENEU_VERSION:
        return "vieneu_version_mismatch"
    try:
        psutil_version = metadata.version("psutil")
    except metadata.PackageNotFoundError:
        return "psutil_missing"
    if psutil_version != REQUIRED_PSUTIL_VERSION:
        return "psutil_version_mismatch"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--allow-local-model", action="store_true", required=True)
    args = parser.parse_args()
    result = run_probe(
        model_path=Path(args.model_path),
        executable=Path(args.executable),
        output_dir=Path(args.output_dir),
        allow_local_model=args.allow_local_model,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {"ok", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
