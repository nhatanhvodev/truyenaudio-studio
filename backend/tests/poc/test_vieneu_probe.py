from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _load_vieneu_probe():
    path = Path(__file__).parents[3] / "scripts" / "poc" / "vieneu_probe.py"
    spec = importlib.util.spec_from_file_location("vieneu_probe_for_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ImportSentinel:
    def __call__(self, name: str) -> str:
        raise AssertionError(f"version lookup should not run before allow flag: {name}")


class SpawnSentinel:
    def __call__(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("spawn should not run")


def test_vieneu_probe_requires_allow_flag_before_optional_imports_or_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_vieneu_probe()
    monkeypatch.setattr(module.metadata, "version", ImportSentinel())
    monkeypatch.setattr(module.subprocess, "Popen", SpawnSentinel())

    result = module.run_probe(
        model_path=tmp_path / "model.bin",
        executable=tmp_path / "vieneu.exe",
        output_dir=tmp_path / "out",
        allow_local_model=False,
    )

    assert result == {
        "status": "blocked",
        "error_code": "POC_LOCAL_MODEL_REQUIRED",
        "redacted_error": "allow_local_model_required",
    }


@pytest.mark.parametrize(
    ("vieneu_version", "psutil_version", "expected"),
    [
        ("3.2.8", "7.0.0", "vieneu_version_mismatch"),
        ("3.2.9", "7.0.1", "psutil_version_mismatch"),
    ],
)
def test_vieneu_probe_blocks_version_mismatch_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vieneu_version: str,
    psutil_version: str,
    expected: str,
) -> None:
    module = _load_vieneu_probe()

    def version(name: str) -> str:
        return {"vieneu": vieneu_version, "psutil": psutil_version}[name]

    monkeypatch.setattr(module.metadata, "version", version)
    monkeypatch.setattr(module.subprocess, "Popen", SpawnSentinel())

    result = module.run_probe(
        model_path=tmp_path / "model.bin",
        executable=tmp_path / "vieneu.exe",
        output_dir=tmp_path / "out",
        allow_local_model=True,
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "MODEL_MISSING"
    assert result["redacted_error"] == expected


def test_run_poc_passes_allow_local_model_flag_only_when_user_allows() -> None:
    script = (Path(__file__).parents[3] / "scripts" / "run-poc.ps1").read_text(encoding="utf-8")

    vieneu_block = script.split('if ($Provider -eq "vieneu") {', maxsplit=1)[1].split('if ($Provider -eq "qwen")', maxsplit=1)[0]
    assert "--allow-local-model" in vieneu_block
    assert "POC_LOCAL_MODEL_REQUIRED" in vieneu_block
