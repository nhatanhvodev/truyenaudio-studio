"""Lắp report JSON cho harness benchmark (task V01).

Report chứa: fixture/seed/fixtureHash, môi trường máy, cấu hình lượt đo, metrics
p50/p95/p99, số câu SQL, bytes response, RSS, WAL và bảng ngưỡng G-PERF với
PASS/FAIL/NOT_RUN (không bao giờ đổi NOT_RUN thành PASS).
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Any

from scripts.benchmarks import harness
from scripts.benchmarks.fixtures import (
    FIXTURE_HASH_DOMAIN,
    FIXTURES,
    FixtureContext,
    FixtureSpec,
    Recorder,
)

__all__ = ["HARNESS_VERSION", "build_report", "fixture_hash", "plan_gperf_rows", "status_summary", "print_fixture_table"]

HARNESS_VERSION = "v01.2"

QUERY_NOTE_DEFAULT = "Số câu SQL mỗi thao tác (SQLAlchemy before/after_cursor_execute)."
RESPONSE_BYTES_NOTE_DEFAULT = "Bytes của response/ payload mỗi thao tác."

PLAN_GPERF_ROWS: dict[str, tuple[str, ...]] = {
    "S1": ("Trang đầu 25 chương cold DB: p95 ≤ 150 ms",),
    "S2": ("Trang 25 chương của project 10k: p95 ≤ 200 ms",),
    "C2K": (
        "Project metadata 1k chương: p95 ≤ 300 ms",
        "Project metadata 10k chương: p95 ≤ 400 ms",
    ),
    "C500": ("C500 metadata RSS: tăng ≤ 256 MiB so với baseline cùng process",),
    "J10K": (
        "Claim 10 client cạnh tranh: p95 ≤ 150 ms",
        "Claim 10 client cạnh tranh: busy ≤ 0,1%",
        "0 duplicate committed claim",
    ),
    "SSE30": (
        "Replay 1k event sau cursor: p95 ≤ 300 ms",
        "Payload event ≤ 16 KiB",
    ),
    "AUDIO500": (
        "0 duplicate READY",
        "checkpoint/part-master resume",
        "seek Range thành công",
    ),
    "CHLIST": (
        "Chapter list/filter: ≤100 mounted rows",
        "Chapter list/filter: server filter/paging",
    ),
    "CHSWITCH": ("Đổi chương cached: p95 <200 ms qua 30 lượt; fetch time báo riêng",),
    "CANCELCKPT": ("Cancel sau checkpoint: p95 ≤5 s; thời gian provider kết thúc báo riêng",),
    "EDITOR8TAB": (
        "C2K editor và 8 tab: không long task >50 ms lặp trong thao tác gõ",
        "C2K editor và 8 tab: không mất draft khi evict/reopen",
    ),
    "SSE30MIN": (
        "SSE 30 phút: store ≤1.000 metadata",
        "SSE 30 phút: delta ≤4 frame/s/job",
        "SSE 30 phút: heap sau GC phút 30 tăng ≤20 MiB so với phút 5",
    ),
    "BACKUP": (
        "Backup: incremental/progress",
        "Backup: integrity/checksum/reference pass",
        "Backup: không copy live WAL riêng",
    ),
}


def fixture_hash(spec: FixtureSpec, seed: int) -> str:
    """sha256 của seed + cấu hình fixture (không phụ thuộc dữ liệu đo)."""

    payload = {
        "domain": FIXTURE_HASH_DOMAIN,
        "fixture": spec.name,
        "seed": int(seed),
        "config": spec.config,
        "harnessVersion": HARNESS_VERSION,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def status_summary(thresholds: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"PASS": 0, "FAIL": 0, "NOT_RUN": 0, "BLOCKED": 0}
    for entry in thresholds:
        status = str(entry.get("status", "NOT_RUN"))
        summary[status] = summary.get(status, 0) + 1
    return summary


def _as_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_report(
    context: FixtureContext,
    recorder: Recorder,
    *,
    started_at: datetime,
    finished_at: datetime,
    guard_report: dict[str, Any],
    wal_bytes: dict[str, Any],
    temp_dir_removed: bool,
) -> dict[str, Any]:
    thresholds = recorder.thresholds
    summary = status_summary(thresholds)
    if summary.get("FAIL"):
        overall = "FAIL"
    elif summary.get("PASS"):
        overall = "PASS"
    else:
        overall = "NOT_RUN"
    spec = context.spec
    return {
        "schemaVersion": 1,
        "fixture": spec.name,
        "fixtureTitle": spec.title,
        "seed": context.seed,
        "fixtureHash": fixture_hash(spec, context.seed),
        "fixtureConfig": spec.config,
        "generatedAt": _as_iso(datetime.now(UTC)),
        "startedAt": _as_iso(started_at),
        "finishedAt": _as_iso(finished_at),
        "durationSeconds": round((finished_at - started_at).total_seconds(), 3),
        "tool": {
            "name": "scripts/benchmarks/run.py",
            "harnessVersion": HARNESS_VERSION,
            "fakeProviders": True,
            "cloudCalls": 0,
            "note": (
                "Harness offline: fixture dựng trên data root tạm, mọi adapter/provider đều fake; "
                "không gọi cloud trả phí, không tải model."
            ),
        },
        "network": guard_report,
        "environment": harness.environment_info(),
        "iterations": context.iterations,
        "warmup": context.warmup,
        "dataRoot": {
            "mode": "temp",
            "temporary": True,
            "tempDirRemoved": temp_dir_removed,
            "note": "Data root tạm dưới tempdir của hệ điều hành; không mở data root thật của studio.",
        },
        "measurement": recorder.measurement,
        "faultMode": context.fault_mode,
        "fakeLatencyMs": context.fake_latency_ms,
        "metrics": recorder.metrics,
        "phases": recorder.phases,
        "queries": {
            "perOperation": recorder.queries,
            "note": recorder.queries_note or QUERY_NOTE_DEFAULT,
            "totals": context.counter.summary(),
        },
        "responseBytes": {
            "perOperation": recorder.response_bytes,
            "note": recorder.response_bytes_note or RESPONSE_BYTES_NOTE_DEFAULT,
        },
        "rssMb": recorder.rss_report(),
        "walBytes": wal_bytes,
        "thresholds": thresholds,
        "statusSummary": summary,
        "overallStatus": overall,
        "extras": recorder.extras,
        "notes": recorder.notes,
    }


def plan_gperf_rows(name: str) -> tuple[str, ...]:
    return PLAN_GPERF_ROWS.get(name, ())


def print_fixture_table() -> None:
    print("Fixture benchmark offline (V01 + V02) — mặc định fake/no-network, data root tạm:")
    for name, spec in FIXTURES.items():
        print(f"  {name:<9} {spec.title}")
        for row in plan_gperf_rows(name):
            print(f"            - {row}")
        print(f"            config: {json.dumps(spec.config, ensure_ascii=False, sort_keys=True)}")
