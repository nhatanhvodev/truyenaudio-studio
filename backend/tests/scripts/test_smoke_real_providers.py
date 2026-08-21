from __future__ import annotations

import json
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "smoke-real-providers.ps1"


def test_qwen_smoke_refuses_multi_segment_before_run(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-Provider",
            "qwen",
            "-AllowPaid",
            "-AuthorizationId",
            "018f0000-0000-7000-8000-000000000701",
            "-CloudConsentId",
            "018f0000-0000-7000-8000-000000000702",
            "-Text",
            "segment one\nsegment two",
            "-ReportRoot",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0
    assert "REFUSED: qwen smoke allows exactly one non-empty segment" in result.stdout
    assert list(tmp_path.glob("*.json")) == []


def test_qwen_smoke_after_run_requires_local_db_context_before_endpoint(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-Provider",
            "qwen",
            "-AllowPaid",
            "-AuthorizationId",
            "018f0000-0000-7000-8000-000000000703",
            "-CloudConsentId",
            "018f0000-0000-7000-8000-000000000704",
            "-Text",
            "single segment",
            "-ReportRoot",
            str(tmp_path),
        ],
        input="RUN\n",
        capture_output=True,
        text=True,
        timeout=20,
    )

    reports = list(tmp_path.glob("real-provider-smoke-*.json"))
    assert result.returncode == 1
    assert "FAIL: QWEN_DB_CONTEXT_REQUIRED" in result.stdout
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8-sig"))
    assert report["status"] == "FAIL"
    assert report["error_code"] == "QWEN_DB_CONTEXT_REQUIRED"
    assert report["provider"] == "qwen"
    assert report["segment_count"] == 1
    assert report["full_text_redacted"] is True


def test_local_smoke_after_run_writes_controlled_fail_when_executable_missing(
    tmp_path: Path,
) -> None:
    missing_executable = tmp_path / "missing-tts.exe"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-Provider",
            "vieneu",
            "-ExecutablePath",
            str(missing_executable),
            "-ReportRoot",
            str(tmp_path),
        ],
        input="RUN\n",
        capture_output=True,
        text=True,
        timeout=20,
    )

    reports = list(tmp_path.glob("real-provider-smoke-*.json"))
    assert result.returncode == 1
    assert "FAIL: LOCAL_TTS_EXECUTABLE_MISSING" in result.stdout
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8-sig"))
    assert report["status"] == "FAIL"
    assert report["error_code"] == "LOCAL_TTS_EXECUTABLE_MISSING"
    assert report["provider"] == "vieneu"
    assert report["full_text_redacted"] is True
