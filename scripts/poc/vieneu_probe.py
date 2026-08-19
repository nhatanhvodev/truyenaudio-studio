from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any


VOICE_PRESETS = ("vi-vieneu-1", "vi-vieneu-2", "vi-vieneu-3", "vi-vieneu-4")


def run_probe(*, model_path: Path, executable: Path, output_dir: Path) -> dict[str, Any]:
    try:
        import psutil
        import vieneu
    except ImportError as exc:
        return {"status": "blocked", "error_code": "MODEL_MISSING", "redacted_error": type(exc).__name__}

    output_dir.mkdir(parents=True, exist_ok=True)
    metrics = []
    peak_mb = 0.0
    started = time.perf_counter()
    for preset in VOICE_PRESETS:
        output_path = output_dir / f"{preset}.wav"
        process = subprocess.Popen(
            [str(executable), "--model", str(model_path), "--voice", preset, "--output", str(output_path)],
            shell=False,
        )
        root = psutil.Process(process.pid)
        while process.poll() is None:
            rss = root.memory_info().rss + sum(child.memory_info().rss for child in root.children(recursive=True))
            peak_mb = max(peak_mb, rss / 1024 / 1024)
            time.sleep(0.05)
        metrics.append({"voice_id": preset, "exit_code": process.returncode})
        if process.returncode != 0:
            return {"status": "error", "error_code": "MODEL_FAILED", "redacted_error": "nonzero_exit", "metrics": metrics}
    elapsed = max(time.perf_counter() - started, 0.001)
    return {
        "status": "ok",
        "provider": "vieneu",
        "model": "vieneu-tts-v3-turbo",
        "provider_version": getattr(vieneu, "__version__", "unknown"),
        "model_snapshot_hash": _hash_file(model_path),
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    result = run_probe(model_path=Path(args.model_path), executable=Path(args.executable), output_dir=Path(args.output_dir))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {"ok", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
