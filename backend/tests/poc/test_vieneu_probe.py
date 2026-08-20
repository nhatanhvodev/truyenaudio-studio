from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
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


class _CompletedProcess:
    pid = 1234
    returncode = 0

    def poll(self) -> int:
        return 0


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

    model_path = tmp_path / "model.bin"
    executable = tmp_path / "vieneu.exe"
    model_path.write_bytes(b"model")
    executable.write_bytes(b"exe")
    monkeypatch.setattr(module.metadata, "version", version)
    monkeypatch.setattr(module.subprocess, "Popen", SpawnSentinel())

    result = module.run_probe(
        model_path=model_path,
        executable=executable,
        output_dir=tmp_path / "out",
        allow_local_model=True,
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "MODEL_MISSING"
    assert result["redacted_error"] == expected


@pytest.mark.parametrize(
    ("model_exists", "executable_exists", "expected"),
    [
        (False, True, "model_missing"),
        (True, False, "executable_missing"),
    ],
)
def test_vieneu_probe_preflights_local_files_before_optional_imports_or_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_exists: bool,
    executable_exists: bool,
    expected: str,
) -> None:
    module = _load_vieneu_probe()
    model_path = tmp_path / "model.onnx"
    executable = tmp_path / "vieneu.exe"
    if model_exists:
        model_path.write_bytes(b"model")
    if executable_exists:
        executable.write_bytes(b"exe")
    monkeypatch.setattr(module.metadata, "version", ImportSentinel())
    monkeypatch.setattr(module.subprocess, "Popen", SpawnSentinel())

    result = module.run_probe(
        model_path=model_path,
        executable=executable,
        output_dir=tmp_path / "out",
        allow_local_model=True,
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "MODEL_MISSING"
    assert result["redacted_error"] == expected
    assert str(tmp_path) not in str(result)


def test_vieneu_probe_returns_redacted_model_failed_when_spawn_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_vieneu_probe()
    model_path = tmp_path / "model.onnx"
    executable = tmp_path / "vieneu.exe"
    model_path.write_bytes(b"model")
    executable.write_bytes(b"exe")
    monkeypatch.setattr(module.metadata, "version", lambda name: {"vieneu": "3.2.9", "psutil": "7.0.0"}[name])
    monkeypatch.setitem(sys.modules, "vieneu", SimpleNamespace(__version__="3.2.9"))
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=lambda pid: object()))

    def raise_oserror(*args: Any, **kwargs: Any) -> None:
        raise OSError(f"cannot execute {executable}")

    monkeypatch.setattr(module.subprocess, "Popen", raise_oserror)

    result = module.run_probe(
        model_path=model_path,
        executable=executable,
        output_dir=tmp_path / "out",
        allow_local_model=True,
    )

    assert result["status"] == "error"
    assert result["error_code"] == "MODEL_FAILED"
    assert result["redacted_error"] == "process_start_failed"
    assert str(tmp_path) not in str(result)


def test_vieneu_probe_returns_redacted_model_failed_when_memory_sampling_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_vieneu_probe()
    model_path = tmp_path / "model.onnx"
    executable = tmp_path / "vieneu.exe"
    model_path.write_bytes(b"model")
    executable.write_bytes(b"exe")
    monkeypatch.setattr(module.metadata, "version", lambda name: {"vieneu": "3.2.9", "psutil": "7.0.0"}[name])
    monkeypatch.setitem(sys.modules, "vieneu", SimpleNamespace(__version__="3.2.9"))

    class Process:
        pid = 1234
        returncode = None

        def __init__(self) -> None:
            self.polls = 0

        def poll(self) -> int | None:
            self.polls += 1
            return None if self.polls == 1 else 0

    class Root:
        def memory_info(self) -> object:
            raise OSError("process disappeared")

        def children(self, recursive: bool = False) -> list[object]:
            return []

    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=lambda pid: Root()))

    result = module.run_probe(
        model_path=model_path,
        executable=executable,
        output_dir=tmp_path / "out",
        allow_local_model=True,
    )

    assert result["status"] == "error"
    assert result["error_code"] == "MODEL_FAILED"
    assert result["redacted_error"] == "process_sampling_failed"
    assert str(tmp_path) not in str(result)


def test_vieneu_probe_returns_redacted_model_failed_when_model_hash_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_vieneu_probe()
    model_path = tmp_path / "model.onnx"
    executable = tmp_path / "vieneu.exe"
    model_path.write_bytes(b"model")
    executable.write_bytes(b"exe")
    monkeypatch.setattr(module.metadata, "version", lambda name: {"vieneu": "3.2.9", "psutil": "7.0.0"}[name])
    monkeypatch.setitem(sys.modules, "vieneu", SimpleNamespace(__version__="3.2.9"))
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=lambda pid: SimpleNamespace()))
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: _CompletedProcess())
    monkeypatch.setattr(module, "_hash_file", lambda path: (_ for _ in ()).throw(OSError("locked")))

    result = module.run_probe(
        model_path=model_path,
        executable=executable,
        output_dir=tmp_path / "out",
        allow_local_model=True,
    )

    assert result["status"] == "error"
    assert result["error_code"] == "MODEL_FAILED"
    assert result["redacted_error"] == "model_snapshot_hash_failed"
    assert str(tmp_path) not in str(result)


def test_run_poc_passes_allow_local_model_flag_only_when_user_allows() -> None:
    script = (Path(__file__).parents[3] / "scripts" / "run-poc.ps1").read_text(encoding="utf-8")

    vieneu_block = script.split('if ($Provider -eq "vieneu") {', maxsplit=1)[1].split('if ($Provider -eq "qwen")', maxsplit=1)[0]
    assert "--allow-local-model" in vieneu_block
    assert "POC_LOCAL_MODEL_REQUIRED" in vieneu_block
