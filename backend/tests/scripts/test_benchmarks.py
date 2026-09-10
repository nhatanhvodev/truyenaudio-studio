"""V01 — harness benchmark offline: CLI, fixture hash, data root tạm và network guard.

Các test ở đây chạy CHÍNH CLI (scripts/benchmarks/run.py) trong tiến trình con với
fixture nhỏ nhất, trên data root tạm, rồi kiểm tra hợp đồng report và các bất biến an
toàn: không chạm data root thật của studio, hash tái lập theo seed, fixture sai thoát
khác 0 và không để lại file rác, guard mạng chặn kết nối ra ngoài loopback.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUN_PY = REPO_ROOT / "scripts" / "benchmarks" / "run.py"
PYTHON = Path(sys.executable)
DEFAULT_DATA_ROOT = REPO_ROOT / "data"

REQUIRED_KEYS = (
    "fixture",
    "seed",
    "fixtureHash",
    "generatedAt",
    "environment",
    "iterations",
    "warmup",
    "metrics",
    "queries",
    "responseBytes",
    "rssMb",
    "walBytes",
    "thresholds",
)

TIMEOUT_SECONDS = 600


def _run_cli(*args: str, timeout: int = TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), str(RUN_PY), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _snapshot_tree(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {str(path.relative_to(root)) for path in root.rglob("*")}


def _quantile_key_metric(metrics: dict[str, object]) -> dict[str, object]:
    for value in metrics.values():
        if isinstance(value, dict) and {"p50", "p95", "p99", "unit", "samples"} <= set(value):
            return value
    raise AssertionError("không metric nào có đủ p50/p95/p99/unit/samples")


def test_cli_s1_self_check_writes_complete_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"

    result = _run_cli("--fixture", "S1", "--seed", "20260908", "--warmup", "1", "--iterations", "2", "--output", str(output))

    assert result.returncode == 0, f"CLI thoát {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    assert output.is_file(), "CLI không ghi report"
    report = json.loads(output.read_text(encoding="utf-8"))

    for key in REQUIRED_KEYS:
        assert key in report, f"report thiếu khóa bắt buộc {key!r}"

    assert report["fixture"] == "S1"
    assert report["seed"] == 20260908
    assert report["warmup"] == 1
    assert report["iterations"] == 2
    assert len(report["fixtureHash"]) == 64
    assert report["generatedAt"].endswith("Z")

    environment = report["environment"]
    for key in ("os", "cpu", "ramTotalMb", "ramAvailableMb", "pythonVersion"):
        assert key in environment, f"environment thiếu {key!r}"
        assert environment[key] not in (None, ""), f"environment.{key} rỗng"
    assert "nodeVersion" in environment

    metrics = report["metrics"]
    assert metrics, "fixture S1 phải sinh ít nhất một metric"
    sample = _quantile_key_metric(metrics)
    assert sample["samples"] == 2
    assert sample["p50"] <= sample["p95"] <= sample["p99"]

    assert report["queries"]["perOperation"], "phải có số câu SQL mỗi thao tác"
    assert report["responseBytes"]["perOperation"], "phải có bytes response mỗi thao tác"
    assert report["rssMb"]["unit"] == "MiB"
    assert {"wal", "shm", "database"} <= set(report["walBytes"])

    assert report["tool"]["cloudCalls"] == 0
    assert report["tool"]["fakeProviders"] is True
    assert report["network"]["blockedAttempts"] == 0
    assert report["dataRoot"]["mode"] == "temp"
    assert report["dataRoot"]["tempDirRemoved"] is True

    thresholds = report["thresholds"]
    assert thresholds, "phải có bảng ngưỡng G-PERF"
    for entry in thresholds:
        assert entry["status"] in {"PASS", "FAIL", "NOT_RUN", "BLOCKED"}
        assert entry["case"]
        if entry["status"] == "NOT_RUN":
            assert entry["note"], "ngưỡng NOT_RUN phải kèm lý do"
        else:
            assert entry["target"] is not None
            assert entry["measured"] is not None
    assert report["statusSummary"]["NOT_RUN"] >= 0
    assert report["overallStatus"] in {"PASS", "FAIL", "NOT_RUN"}


def test_fixture_hash_is_seed_and_config_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "same-a.json"
    second = tmp_path / "same-b.json"
    other = tmp_path / "other-seed.json"

    for output in (first, second):
        result = _run_cli("--fixture", "S1", "--seed", "20260908", "--warmup", "0", "--iterations", "1", "--output", str(output))
        assert result.returncode == 0, result.stderr

    result = _run_cli("--fixture", "S1", "--seed", "20260909", "--warmup", "0", "--iterations", "1", "--output", str(other))
    assert result.returncode == 0, result.stderr

    hash_same_a = json.loads(first.read_text(encoding="utf-8"))["fixtureHash"]
    hash_same_b = json.loads(second.read_text(encoding="utf-8"))["fixtureHash"]
    hash_other = json.loads(other.read_text(encoding="utf-8"))["fixtureHash"]

    assert hash_same_a == hash_same_b, "cùng seed + cùng cấu hình phải cho cùng fixtureHash"
    assert hash_same_a != hash_other, "khác seed phải cho fixtureHash khác"


def test_unknown_fixture_fails_without_writing_output(tmp_path: Path) -> None:
    output = tmp_path / "should-not-exist.json"
    before = _snapshot_tree(tmp_path)

    result = _run_cli("--fixture", "KHONG_TON_TAI", "--seed", "20260908", "--warmup", "1", "--iterations", "1", "--output", str(output))

    assert result.returncode != 0, "fixture không tồn tại phải thoát khác 0"
    assert result.returncode != 0
    assert not output.exists(), "không được sinh report cho fixture không tồn tại"
    assert _snapshot_tree(tmp_path) == before, "không được để lại file rác"
    assert "KHONG_TON_TAI" in (result.stderr + result.stdout)


def test_harness_never_touches_real_data_root(tmp_path: Path) -> None:
    before = _snapshot_tree(DEFAULT_DATA_ROOT)
    output = tmp_path / "report.json"

    result = _run_cli("--fixture", "S1", "--seed", "20260908", "--warmup", "0", "--iterations", "1", "--output", str(output))

    assert result.returncode == 0, result.stderr
    assert output.is_file()
    assert _snapshot_tree(DEFAULT_DATA_ROOT) == before, "harness đã ghi vào data root thật của studio"

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["dataRoot"]["temporary"] is True

    # Cấu hình mặc định của app trỏ vào data root thật; harness phải luôn override bằng tmp.
    from app.settings.config import Settings

    assert Settings().data_root == DEFAULT_DATA_ROOT


def test_invalid_inputs_exit_non_zero_without_output(tmp_path: Path) -> None:
    output = tmp_path / "never.json"

    bad_iterations = _run_cli("--fixture", "S1", "--iterations", "0", "--output", str(output))
    assert bad_iterations.returncode != 0
    assert not output.exists()

    missing_fixture = _run_cli("--seed", "20260908", "--output", str(output))
    assert missing_fixture.returncode != 0
    assert not output.exists()


def test_network_guard_blocks_non_loopback_connections() -> None:
    from scripts.benchmarks.network_guard import (
        NetworkBlockedError,
        install_network_guard,
        is_loopback_address,
    )

    assert is_loopback_address(("127.0.0.1", 80)) is True
    assert is_loopback_address(("::1", 443, 0, 0)) is True
    assert is_loopback_address(("localhost", 8765)) is True
    assert is_loopback_address(("example.com", 443)) is False
    assert is_loopback_address(("93.184.216.34", 443)) is False

    guard = install_network_guard()
    try:
        with pytest.raises(NetworkBlockedError):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("93.184.216.34", 443))

        with pytest.raises(NetworkBlockedError):
            socket.create_connection(("example.com", 443), timeout=1)

        with pytest.raises(NetworkBlockedError):
            socket.getaddrinfo("example.com", 443)

        with pytest.raises(NetworkBlockedError):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect_ex(("198.51.100.7", 80))

        assert guard.blocked_count == 4
        targets = guard.blocked_targets()
        assert any("example.com" in target for target in targets)
        report = guard.as_report()
        assert report["mode"] == "offline-guard"
        assert report["blockedAttempts"] == 4
    finally:
        guard.uninstall()

    # Sau khi gỡ, kết nối loopback vẫn phải hoạt động bình thường.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(5)
        client.connect(listener.getsockname())
        client.close()
    finally:
        listener.close()


def test_benchmark_cli_source_never_calls_cloud_providers() -> None:
    """Bất biến tĩnh: harness không import adapter cloud và không có endpoint ngoài."""

    package = RUN_PY.parent
    offline_cli = {"run.py", "fixtures.py", "harness.py", "report.py", "stats.py", "network_guard.py", "__init__.py"}
    sources = {path.name: path.read_text(encoding="utf-8") for path in package.glob("*.py")}
    assert set(sources) == offline_cli

    joined = "\n".join(sources.values())
    for forbidden in ("qwen_mt", "gemini_mt", "elevenlabs", "openai", "api_key", "Authorization: Bearer"):
        assert forbidden not in joined, f"harness không được tham chiếu {forbidden!r}"
    assert "install_network_guard" in sources["run.py"]

    hasher = hashlib.sha256()
    for name in sorted(sources):
        hasher.update(name.encode("utf-8"))
        hasher.update(sources[name].encode("utf-8"))
    assert hasher.hexdigest()
