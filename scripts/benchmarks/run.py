"""CLI benchmark offline cho Truyện Audio Studio (task V01).

Giao diện bắt buộc (plan §7):

    python scripts/benchmarks/run.py --fixture S1|S2|C500|C2K|J10K|SSE30|AUDIO500 \
        --seed 20260908 --warmup 5 --iterations 20 --output <report.json>

V02 bổ sung 6 fixture cho các dòng G-PERF còn thiếu fixture (plan §7):

    --fixture CHLIST      chapter list/filter: ≤100 mounted rows, server filter/paging
    --fixture CHSWITCH    đổi chương cached: p95 <200 ms qua 30 lượt, fetch báo riêng
    --fixture CANCELCKPT  cancel sau checkpoint: p95 ≤5 s, provider báo riêng
    --fixture EDITOR8TAB  C2K editor 8 tab: cap 8 tab + draft khi evict/reopen (long task NOT_RUN)
    --fixture SSE30MIN    phiên SSE rút ngắn: store ≤1.000 metadata, ≤4 frame/s/job (30 phút NOT_RUN)
    --fixture BACKUP      backup thật: integrity/checksum/reference, không copy live WAL riêng

Hai cờ chỉ dùng cho fixture V02 (mặc định giữ nguyên hành vi V01): --scale và --session-seconds.

Mặc định: fake/no-network và data root TẠM. CLI cài network guard trước khi dựng
fixture; nếu có ý định kết nối ra ngoài loopback thì thoát khác 0 và ghi rõ.

Mã thoát: 0 = đã chạy và ghi report (kể cả khi có ngưỡng FAIL, trừ khi bật
--fail-on-threshold), 2 = tham số/fixture sai, 3 = có ngưỡng FAIL và bật
--fail-on-threshold, 4 = lỗi harness hoặc phát hiện gọi mạng.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
import sys
import traceback

if __package__ in (None, ""):  # chạy trực tiếp: python scripts/benchmarks/run.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.benchmarks import harness  # noqa: E402
from scripts.benchmarks.fixtures import (  # noqa: E402
    DEFAULT_SEED,
    FIXTURES,
    FixtureContext,
    Recorder,
    fixture_names,
)
from scripts.benchmarks.network_guard import NetworkBlockedError, install_network_guard  # noqa: E402
from scripts.benchmarks.report import build_report, print_fixture_table  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_THRESHOLD = 3
EXIT_HARNESS = 4


def configure_stdio() -> None:
    """Ép stdout/stderr sang UTF-8 để câu chữ tiếng Việt không vỡ trên console Windows (cp1252)."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - phụ thuộc môi trường
            continue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/benchmarks/run.py",
        description=(
            "Harness benchmark offline cho Truyện Audio Studio (V01). "
            "Fixture dựng dữ liệu thật trên data root tạm, không gọi mạng/cloud."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--fixture", help="Fixture cần chạy: " + ", ".join(fixture_names()))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"Seed tái lập (mặc định {DEFAULT_SEED}).")
    parser.add_argument("--warmup", type=int, default=0, help="Số lượt warm-up bị loại khỏi mẫu đo.")
    parser.add_argument("--iterations", type=int, default=1, help="Số lượt đo được ghi vào metrics.")
    parser.add_argument("--output", type=Path, help="Đường dẫn file JSON report.")
    parser.add_argument("--list", action="store_true", help="Liệt kê fixture và ngưỡng G-PERF áp dụng rồi thoát.")
    parser.add_argument("--json", action="store_true", help="In report JSON đầy đủ ra stdout.")
    parser.add_argument("--keep-temp", action="store_true", help="Giữ lại data root tạm để điều tra.")
    parser.add_argument(
        "--fail-on-threshold",
        action="store_true",
        help="Thoát mã 3 nếu có ngưỡng FAIL (mặc định vẫn thoát 0 và ghi FAIL vào report).",
    )
    parser.add_argument(
        "--fault-mode",
        choices=("none", "fail-first-attempt"),
        default="none",
        help="Chế độ lỗi giả lập cho AUDIO500 (lỗi giữa lượt ghi để kiểm tra resume).",
    )
    parser.add_argument(
        "--fake-latency-ms",
        type=int,
        default=0,
        help="Độ trễ GIẢ LẬP mỗi segment ở pha provider (chỉ để chứng minh tách pha).",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help=(
            "Hệ số kích thước dữ liệu cho fixture V02 (mặc định 1.0 = đúng cấu hình fixture). "
            "Fixture V01 bỏ qua cờ này."
        ),
    )
    parser.add_argument(
        "--session-seconds",
        type=float,
        default=0.0,
        help=(
            "Độ dài phiên SSE của SSE30MIN (mặc định 0 = dùng cấu hình fixture). Plan §7 yêu cầu 1.800 s; "
            "giá trị nhỏ hơn được ghi NOT_RUN kèm số đo rút ngắn."
        ),
    )
    return parser


def _fail(message: str) -> int:
    print(f"[benchmark] LỖI: {message}", file=sys.stderr)
    return EXIT_USAGE


def _wal_report(data_root: Path) -> dict[str, object]:
    report = harness.wal_sizes(data_root)
    sub: dict[str, object] = {}
    for candidate in sorted(Path(data_root).rglob(harness.DATABASE_NAME)):
        if candidate.parent != Path(data_root):
            sub[candidate.parent.relative_to(data_root).as_posix()] = harness.wal_sizes(candidate.parent)
    if sub:
        report["subDatabases"] = sub
    return report


def _print_summary(report: dict[str, object]) -> None:
    print(f"Fixture {report['fixture']} — {report['fixtureTitle']}")
    print(f"  seed={report['seed']} fixtureHash={report['fixtureHash']}")
    print(f"  warmup={report['warmup']} iterations={report['iterations']} thời lượng={report['durationSeconds']}s")
    print("  Metrics:")
    metrics = report["metrics"]
    assert isinstance(metrics, dict)
    for name, entry in metrics.items():
        if not isinstance(entry, dict):
            continue
        print(
            f"    - {name}: p50={entry.get('p50')} p95={entry.get('p95')} p99={entry.get('p99')} "
            f"{entry.get('unit')} (n={entry.get('samples')})"
        )
    print("  Ngưỡng G-PERF:")
    for entry in report["thresholds"]:  # type: ignore[index]
        measured = entry.get("measured")
        print(f"    - [{entry.get('status')}] {entry.get('case')} | {entry.get('targetLabel')} | đo được: {measured} {entry.get('unit') or ''}")
        if entry.get("status") == "NOT_RUN" and entry.get("note"):
            print(f"        lý do: {entry['note']}")
    summary = report["statusSummary"]
    print(f"  Tổng: {summary} => {report['overallStatus']}")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        print_fixture_table()
        return EXIT_OK

    if not args.fixture:
        return _fail("thiếu --fixture. Dùng --list để xem danh sách fixture.")
    if args.fixture not in FIXTURES:
        return _fail(
            f"fixture không tồn tại: {args.fixture!r}. Fixture hợp lệ: {', '.join(fixture_names())}."
        )
    if args.warmup < 0:
        return _fail("--warmup phải >= 0.")
    if args.iterations < 1:
        return _fail("--iterations phải >= 1.")
    if args.fake_latency_ms < 0:
        return _fail("--fake-latency-ms phải >= 0.")
    if args.scale <= 0:
        return _fail("--scale phải > 0.")
    if args.session_seconds < 0:
        return _fail("--session-seconds phải >= 0.")

    spec = FIXTURES[args.fixture]
    guard = install_network_guard()
    counter = harness.SqlPhaseCounter().attach()
    started_at = datetime.now(UTC)
    temp_dir_removed = False
    report: dict[str, object] | None = None
    exit_code = EXIT_OK

    try:
        with harness.temp_data_root(prefix=f"studio-bench-{spec.name.lower()}-", keep=args.keep_temp) as data_root:
            context = FixtureContext(
                spec=spec,
                seed=args.seed,
                warmup=args.warmup,
                iterations=args.iterations,
                data_root=data_root,
                counter=counter,
                fault_mode=args.fault_mode,
                fake_latency_ms=args.fake_latency_ms,
                scale=args.scale,
                session_seconds=args.session_seconds,
            )
            recorder = Recorder()
            recorder.mark_rss("baseline")
            spec.runner(context, recorder)
            recorder.mark_rss("final")
            wal_bytes = _wal_report(data_root)
        temp_dir_removed = not args.keep_temp
    except NetworkBlockedError as exc:
        print(f"[benchmark] LỖI: {exc}", file=sys.stderr)
        return EXIT_HARNESS
    except Exception as exc:  # noqa: BLE001 - CLI phải báo lỗi rõ ràng rồi thoát khác 0
        print(f"[benchmark] LỖI harness: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return EXIT_HARNESS
    finally:
        counter.detach()

    finished_at = datetime.now(UTC)
    report = build_report(
        context,
        recorder,
        started_at=started_at,
        finished_at=finished_at,
        guard_report=guard.as_report(),
        wal_bytes=wal_bytes,
        temp_dir_removed=temp_dir_removed,
    )

    if guard.blocked_count:
        print(
            "[benchmark] LỖI: phát hiện ý định gọi mạng trong fixture "
            f"({', '.join(guard.blocked_targets())}).",
            file=sys.stderr,
        )
        return EXIT_HARNESS

    if args.output is not None:
        output = Path(args.output)
        if output.parent and not output.parent.exists():
            output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[benchmark] đã ghi report: {output}")

    _print_summary(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if report["statusSummary"]["FAIL"] and args.fail_on_threshold:  # type: ignore[index]
        exit_code = EXIT_THRESHOLD
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
