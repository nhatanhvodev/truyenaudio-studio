"""Fixture và phép đo cho harness benchmark offline (task V01).

Mỗi fixture dựng dữ liệu bằng chính ORM/migration của app trên data root TẠM và đo
bằng code thật của app (router FastAPI thật, JobRunner thật, RecoveryArtifactWriter
thật). Không fixture nào gọi cloud, tải model hay chạm data root thật của studio.

Phân tách pha (theo rủi ro "đo lẫn provider latency" trong plan §7): mọi metric DB
được đo bằng listener SQLAlchemy before/after_cursor_execute, nên thời gian
provider/FFmpeg (nếu có) không bao giờ nằm trong budget API/DB.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import json
import random
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.events import _events
from app.api.projects import create_projects_router
from app.contracts import (
    ArtifactKind,
    ChapterState,
    JobKind,
    JobStatus,
    RightsStatus,
    SourceType,
)
from app.db.base import session_factory
from app.db.models import Chapter, Job, Project
from app.modules.jobs.recovery import RecoveryArtifactWriter
from app.modules.jobs.runner import JobRunner
from app.modules.projects.queries import ChapterQueries
from app.settings.config import Settings

from scripts.benchmarks import harness
from scripts.benchmarks.stats import percentile, summarize

__all__ = ["FIXTURES", "FixtureContext", "FixtureSpec", "Recorder", "fixture_names"]

DEFAULT_SEED = 20260908
CURSOR_SECRET = "benchmark-cursor-secret"
BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
STATEMENT_DIGITS = 1
BYTE_DIGITS = 1

FIXTURE_HASH_DOMAIN = "truyenaudio-studio/benchmarks/v1"


class FixtureError(RuntimeError):
    """Fixture không dựng được dữ liệu hoặc thao tác trả về sai."""


# --------------------------------------------------------------------------- #
# Recorder
# --------------------------------------------------------------------------- #


@dataclass
class Recorder:
    """Thu thập mẫu đo, ngưỡng G-PERF, pha và số liệu phụ của MỘT fixture."""

    metrics: dict[str, dict[str, object]] = field(default_factory=dict)
    samples: dict[str, list[float]] = field(default_factory=dict)
    thresholds: list[dict[str, object]] = field(default_factory=list)
    phases: dict[str, dict[str, object]] = field(default_factory=dict)
    queries: dict[str, dict[str, object]] = field(default_factory=dict)
    response_bytes: dict[str, dict[str, object]] = field(default_factory=dict)
    extras: dict[str, object] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    measurement: dict[str, object] = field(default_factory=dict)
    queries_note: str | None = None
    response_bytes_note: str | None = None
    _rss_baseline: float | None = field(default=None, repr=False)
    _rss_peak: float | None = field(default=None, repr=False)
    _rss_marks: dict[str, float] = field(default_factory=dict, repr=False)

    # -- metrics ---------------------------------------------------------- #

    def record(self, name: str, values: list[float], unit: str, *, digits: int = 3, note: str | None = None) -> None:
        if not values:
            raise FixtureError(f"metric {name} không có mẫu nào")
        self.samples.setdefault(name, []).extend(float(value) for value in values)
        entry = summarize(self.samples[name], unit, digits=digits)
        if note:
            entry["note"] = note
        self.metrics[name] = entry

    def p(self, name: str, quantile: float) -> float:
        if name not in self.samples:
            raise FixtureError(f"metric {name} chưa được đo")
        return percentile(self.samples[name], quantile)

    def metric_max(self, name: str) -> float:
        return max(self.samples[name])

    def add_query(self, label: str, statements: list[float], unit: str = "statements") -> None:
        if not statements:
            return
        self.queries[label] = summarize(statements, unit, digits=STATEMENT_DIGITS)

    def add_response_bytes(self, label: str, payloads: list[float]) -> None:
        if not payloads:
            return
        self.response_bytes[label] = summarize(payloads, "bytes", digits=BYTE_DIGITS)

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    def set_measurement(self, key: str, value: object) -> None:
        """Mô tả thao tác đã đo (hiển thị nguyên văn trong report)."""

        self.measurement[key] = value

    # -- thresholds ------------------------------------------------------- #

    def threshold_le(
        self,
        *,
        case: str,
        target: float,
        measured: float,
        unit: str,
        label: str,
        note: str | None = None,
    ) -> None:
        status = "PASS" if measured <= target else "FAIL"
        entry: dict[str, object] = {
            "case": case,
            "target": target,
            "targetLabel": label,
            "measured": round(float(measured), 3),
            "unit": unit,
            "status": status,
        }
        if note:
            entry["note"] = note
        self.thresholds.append(entry)

    def threshold_bool(
        self,
        *,
        case: str,
        ok: bool,
        measured: object,
        unit: str,
        label: str,
        target: object = None,
        note: str | None = None,
    ) -> None:
        entry: dict[str, object] = {
            "case": case,
            "target": target,
            "targetLabel": label,
            "measured": measured,
            "unit": unit,
            "status": "PASS" if ok else "FAIL",
        }
        if note:
            entry["note"] = note
        self.thresholds.append(entry)

    def threshold_not_run(self, *, case: str, label: str, reason: str, unit: str | None = None) -> None:
        self.thresholds.append(
            {
                "case": case,
                "target": None,
                "targetLabel": label,
                "measured": None,
                "unit": unit,
                "status": "NOT_RUN",
                "note": reason,
            }
        )

    # -- phases ----------------------------------------------------------- #

    def set_phase(self, name: str, status: str, *, metric: str | None = None, note: str | None = None) -> None:
        entry: dict[str, object] = {"status": status}
        if metric:
            entry["metric"] = metric
        if note:
            entry["note"] = note
        self.phases[name] = entry

    # -- rss -------------------------------------------------------------- #

    def mark_rss(self, label: str) -> float:
        harness.gc_collect()
        value = harness.rss_mb()
        self._rss_marks[label] = value
        if self._rss_baseline is None:
            self._rss_baseline = value
        self._rss_peak = value if self._rss_peak is None else max(self._rss_peak, value)
        return value

    def sample_rss(self) -> float:
        return self.mark_rss("sample")

    def rss_report(self) -> dict[str, object]:
        baseline = self._rss_baseline if self._rss_baseline is not None else harness.rss_mb()
        peak = self._rss_peak if self._rss_peak is not None else baseline
        marks = {key: round(value, 2) for key, value in self._rss_marks.items() if key != "sample"}
        return {
            "baseline": round(baseline, 2),
            "peak": round(peak, 2),
            "delta": round(max(0.0, peak - baseline), 2),
            "marks": marks,
            "unit": "MiB",
            "source": "psutil.Process().memory_info().rss — cùng tiến trình CLI",
        }


# --------------------------------------------------------------------------- #
# Fixture context / spec
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    title: str
    config: dict[str, object]
    runner: Callable[["FixtureContext", Recorder], None]


@dataclass
class FixtureContext:
    spec: FixtureSpec
    seed: int
    warmup: int
    iterations: int
    data_root: Path
    counter: harness.SqlPhaseCounter
    fault_mode: str = "none"
    fake_latency_ms: int = 0

    @property
    def config(self) -> dict[str, object]:
        return self.spec.config


# --------------------------------------------------------------------------- #
# Tiện ích dựng dữ liệu
# --------------------------------------------------------------------------- #


def _project_id(index: int) -> str:
    return f"018f0000-0000-7000-8000-a{index:011d}"


def _chapter_id(project_index: int, ordinal: int) -> str:
    return f"018f0000-0000-7000-8000-b{project_index:02d}{ordinal:09d}"


def _job_id(index: int) -> str:
    return f"018f0000-0000-7000-8000-c{index:011d}"


def _fake_payload(seed: int, index: int, size: int) -> bytes:
    digest = hashlib.sha256(f"{seed}:{index}".encode("utf-8")).digest()
    repeats = (size // len(digest)) + 1
    return (digest * repeats)[:size]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def seed_projects(
    session: Any,
    *,
    seed: int,
    fixture_name: str,
    project_count: int,
    chapters_per_project: int,
) -> list[str]:
    """Chèn project/chapter bằng chính bảng của app (bulk insert, metadata-only)."""

    projects: list[dict[str, object]] = []
    chapters: list[dict[str, object]] = []
    project_ids: list[str] = []
    for index in range(project_count):
        rng = random.Random(f"{seed}:{fixture_name}:{index}")
        project_id = _project_id(index)
        created = BASE_TIME + timedelta(seconds=index)
        project_ids.append(project_id)
        projects.append(
            {
                "id": project_id,
                "title": f"Truyện {index + 1} {rng.randrange(0, 1 << 30):08x}",
                "slug": f"bench-{fixture_name.lower()}-{index + 1}",
                "source_type": SourceType.USER_SUPPLIED_PRIVATE.value,
                "rights_status": RightsStatus.PRIVATE_ONLY.value,
                "default_language": "zh-CN",
                "target_language": "vi-VN",
                "monthly_chapter_target": 50,
                "created_at": created,
                "updated_at": created,
            }
        )
        for ordinal in range(1, chapters_per_project + 1):
            chapters.append(
                {
                    "id": _chapter_id(index, ordinal),
                    "project_id": project_id,
                    "ordinal": ordinal,
                    "source_title": f"Chương {ordinal} · {rng.randrange(0, 1 << 20):05x}",
                    "translated_title": None,
                    "state": ChapterState.IMPORTED.value,
                    "created_at": created,
                    "updated_at": created,
                }
            )
    if projects:
        session.execute(insert(Project), projects)
    if chapters:
        session.execute(insert(Chapter), chapters)
    session.commit()
    return project_ids


def seed_jobs(engine: Engine, *, count: int, project_id: str, fixture_name: str) -> None:
    created = BASE_TIME + timedelta(hours=1)
    rows = [
        {
            "id": _job_id(index),
            "kind": JobKind.TRANSLATE.value,
            "status": JobStatus.QUEUED.value,
            "project_id": project_id,
            "chapter_id": None,
            "idempotency_key": f"{fixture_name.lower()}-{index}",
            "priority": 100,
            "progress_current": 0,
            "progress_total": 0,
            "created_at": created,
            "updated_at": created,
        }
        for index in range(count)
    ]
    with engine.begin() as connection:
        connection.execute(insert(Job), rows)


# --------------------------------------------------------------------------- #
# Tiện ích đo
# --------------------------------------------------------------------------- #


@contextmanager
def operation(context: FixtureContext) -> Iterator[dict[str, float]]:
    """Một thao tác đo: tổng thời gian, thời gian DB và số câu SQL."""

    box: dict[str, Any] = {
        "elapsedMs": 0.0,
        "dbMs": 0.0,
        "statements": 0.0,
        "bytes": 0.0,
        "dbAttribution": "thread-local",
    }
    token = context.counter.begin_op()
    started = time.perf_counter()
    try:
        yield box
    finally:
        elapsed = (time.perf_counter() - started) * 1000.0
        db_seconds, statements, attribution = context.counter.end_op(token)
        box["elapsedMs"] = elapsed
        box["dbMs"] = db_seconds * 1000.0
        box["statements"] = float(statements)
        box["dbAttribution"] = attribution


def _api_client(data_root: Path) -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    app.include_router(create_projects_router(Settings(data_root=data_root), cursor_secret=CURSOR_SECRET))
    return app, TestClient(app)


def _attribution_note(attributions: list[str]) -> str:
    unique = sorted(set(attributions))
    if unique == ["thread-local"]:
        return "Thời gian trong SQLite, đo trên chính thread gọi thao tác (thread-local)."
    if unique == ["process-global-delta"]:
        return (
            "Thời gian trong SQLite, quy kết bằng chênh lệch bộ đếm toàn tiến trình "
            "(process-global-delta) vì TestClient chạy ASGI app ở thread khác; fixture chạy tuần tự "
            "một thao tác một lượt nên chênh lệch này chính là thao tác."
        )
    return f"Thời gian trong SQLite, cách quy kết: {', '.join(unique)}."


def _record_api_metrics(
    recorder: Recorder,
    *,
    label: str,
    total: list[float],
    db_ms: list[float],
    statements: list[float],
    payloads: list[float],
    attributions: list[str],
    note: str | None = None,
) -> None:
    recorder.record(f"{label}.total", total, "ms", note=note)
    recorder.record(f"{label}.db", db_ms, "ms", note=_attribution_note(attributions))
    recorder.record(
        f"{label}.other",
        [total_value - db_value for total_value, db_value in zip(total, db_ms, strict=False)],
        "ms",
        note="Phần còn lại: FastAPI/ORM/serialize — không gồm provider/FFmpeg.",
    )
    recorder.record(f"{label}.statements", statements, "statements", digits=STATEMENT_DIGITS)
    recorder.record(f"{label}.responseBytes", payloads, "bytes", digits=BYTE_DIGITS)
    recorder.add_query(label, statements)
    recorder.add_response_bytes(label, payloads)


def _measure_chapter_page(
    context: FixtureContext,
    recorder: Recorder,
    *,
    data_root: Path,
    project_id: str,
    limit: int,
    label: str,
    cursor: str | None = None,
    note: str | None = None,
) -> None:
    _app, client = _api_client(data_root)
    url = f"/api/projects/{project_id}/chapters?limit={limit}"
    if cursor is not None:
        url = f"{url}&cursor={quote(cursor)}"
    total: list[float] = []
    db_ms: list[float] = []
    statements: list[float] = []
    payloads: list[float] = []
    attributions: list[str] = []
    with client:
        for index in range(context.warmup + context.iterations):
            with operation(context) as box:
                response = client.get(url)
                box["bytes"] = float(len(response.content))
            if response.status_code != 200:
                raise FixtureError(
                    f"{label}: GET {url} trả về HTTP {response.status_code}: {response.text[:200]}"
                )
            if index < context.warmup:
                continue
            total.append(box["elapsedMs"])
            db_ms.append(box["dbMs"])
            statements.append(box["statements"])
            payloads.append(box["bytes"])
            attributions.append(str(box["dbAttribution"]))
    _record_api_metrics(
        recorder,
        label=label,
        total=total,
        db_ms=db_ms,
        statements=statements,
        payloads=payloads,
        attributions=attributions,
        note=note,
    )


def _measure_library_page(
    context: FixtureContext,
    recorder: Recorder,
    *,
    data_root: Path,
    label: str,
    limit: int,
    sample_rss: bool = False,
    note: str | None = None,
) -> None:
    _app, client = _api_client(data_root)
    url = f"/api/projects?limit={limit}"
    total: list[float] = []
    db_ms: list[float] = []
    statements: list[float] = []
    payloads: list[float] = []
    attributions: list[str] = []
    with client:
        for index in range(context.warmup + context.iterations):
            with operation(context) as box:
                response = client.get(url)
                box["bytes"] = float(len(response.content))
            if response.status_code != 200:
                raise FixtureError(
                    f"{label}: GET {url} trả về HTTP {response.status_code}: {response.text[:200]}"
                )
            if sample_rss:
                recorder.sample_rss()
            if index < context.warmup:
                continue
            total.append(box["elapsedMs"])
            db_ms.append(box["dbMs"])
            statements.append(box["statements"])
            payloads.append(box["bytes"])
            attributions.append(str(box["dbAttribution"]))
    _record_api_metrics(
        recorder,
        label=label,
        total=total,
        db_ms=db_ms,
        statements=statements,
        payloads=payloads,
        attributions=attributions,
        note=note,
    )


def _set_provider_phases(recorder: Recorder, *, fake_latency_metric: str | None = None) -> None:
    if fake_latency_metric is None:
        recorder.set_phase(
            "provider",
            "NOT_RUN",
            note="Fixture chạy offline: không gọi provider/cloud nào (không có số đo provider thật).",
        )
    else:
        recorder.set_phase(
            "provider",
            "MEASURED",
            metric=fake_latency_metric,
            note=(
                "Latency GIẢ LẬP (--fake-latency-ms), chỉ chứng minh plumbing và tách pha; "
                "KHÔNG phải provider thật và không nằm trong budget API/DB."
            ),
        )
    recorder.set_phase(
        "ffmpeg",
        "NOT_RUN",
        note="Không chạy FFmpeg: payload tổng hợp, không cài model/FFmpeg trong fixture.",
    )


# --------------------------------------------------------------------------- #
# Fixture S1 / S2 / C2K / C500 — metadata qua router thật
# --------------------------------------------------------------------------- #


def run_s1(context: FixtureContext, recorder: Recorder) -> None:
    config = context.config
    engine = harness.bootstrap_database(context.data_root)
    try:
        with session_factory(engine)() as session:
            project_ids = seed_projects(
                session,
                seed=context.seed,
                fixture_name=context.spec.name,
                project_count=int(config["projects"]),
                chapters_per_project=int(config["chapters"]),
            )
        _measure_chapter_page(
            context,
            recorder,
            data_root=context.data_root,
            project_id=project_ids[0],
            limit=int(config["pageSize"]),
            label="chapterPage25",
            note="Mỗi request tạo engine mới rồi dispose (đúng code route) => đo trạng thái cold DB.",
        )
    finally:
        engine.dispose()

    recorder.set_measurement(
        "operation",
        "GET /api/projects/{id}/chapters?limit=25 trên DB tạm vừa dựng (cold: engine mới mỗi request).",
    )
    recorder.set_phase("db", "MEASURED", metric="chapterPage25.db")
    recorder.set_phase("queue", "NOT_RUN", note="S1 không đi qua worker queue (đọc metadata trực tiếp).")
    _set_provider_phases(recorder)
    recorder.threshold_le(
        case="Trang đầu 25 chương cold DB",
        target=150,
        measured=recorder.p("chapterPage25.total", 0.95),
        unit="ms",
        label="p95 ≤ 150 ms",
        note="G-PERF (plan §7): p95 của cả request HTTP in-process, đã tách phần DB.",
    )


def run_s2(context: FixtureContext, recorder: Recorder) -> None:
    config = context.config
    engine = harness.bootstrap_database(context.data_root)
    try:
        with session_factory(engine)() as session:
            project_ids = seed_projects(
                session,
                seed=context.seed,
                fixture_name=context.spec.name,
                project_count=int(config["projects"]),
                chapters_per_project=int(config["chapters"]),
            )
        start_ordinal = int(config["startOrdinal"])
        cursor = ChapterQueries(engine, cursor_secret=CURSOR_SECRET)._encode_cursor(
            start_ordinal, _chapter_id(0, start_ordinal)
        )
        _measure_chapter_page(
            context,
            recorder,
            data_root=context.data_root,
            project_id=project_ids[0],
            limit=int(config["pageSize"]),
            label="chapterPage25Deep",
            cursor=cursor,
            note=(
                f"Trang 25 chương bắt đầu ở ordinal {start_ordinal} của project "
                f"{int(config['chapters'])} chương; cursor sinh bằng chính ChapterQueries._encode_cursor "
                "với cùng secret như router."
            ),
        )
    finally:
        engine.dispose()

    recorder.set_measurement(
        "operation",
        "GET /api/projects/{id}/chapters?limit=25&cursor=<trang sâu> trên project 10k chương (data root tạm).",
    )
    recorder.set_phase("db", "MEASURED", metric="chapterPage25Deep.db")
    recorder.set_phase("queue", "NOT_RUN", note="S2 không đi qua worker queue.")
    _set_provider_phases(recorder)
    recorder.threshold_le(
        case="Trang 25 chương của project 10k",
        target=200,
        measured=recorder.p("chapterPage25Deep.total", 0.95),
        unit="ms",
        label="p95 ≤ 200 ms",
        note="G-PERF (plan §7).",
    )


def run_c2k(context: FixtureContext, recorder: Recorder) -> None:
    config = context.config
    for count in [int(value) for value in config["chapterCounts"]]:
        sub_root = context.data_root / f"metadata-{count}"
        engine = harness.bootstrap_database(sub_root)
        try:
            with session_factory(engine)() as session:
                seed_projects(
                    session,
                    seed=context.seed,
                    fixture_name=f"{context.spec.name}-{count}",
                    project_count=int(config["projects"]),
                    chapters_per_project=count,
                )
            _measure_library_page(
                context,
                recorder,
                data_root=sub_root,
                label=f"libraryMetadata{count}",
                limit=int(config["pageSize"]),
                note=(
                    f"GET /api/projects?limit={int(config['pageSize'])} trên project {count} chương "
                    "(DB riêng cho từng kích thước, cùng schema migrate head)."
                ),
            )
        finally:
            engine.dispose()

    recorder.set_measurement(
        "operation",
        "GET /api/projects?limit=20 (metadata thư viện: đếm chương + 30 chương mount/project) ở 1k và 10k chương.",
    )
    recorder.set_phase("db", "MEASURED", metric="libraryMetadata1000.db")
    recorder.set_phase("queue", "NOT_RUN", note="C2K không đi qua worker queue.")
    _set_provider_phases(recorder)
    recorder.threshold_le(
        case="Project metadata 1k chương",
        target=300,
        measured=recorder.p("libraryMetadata1000.total", 0.95),
        unit="ms",
        label="p95 ≤ 300 ms",
        note="G-PERF (plan §7).",
    )
    recorder.threshold_le(
        case="Project metadata 10k chương",
        target=400,
        measured=recorder.p("libraryMetadata10000.total", 0.95),
        unit="ms",
        label="p95 ≤ 400 ms",
        note="G-PERF (plan §7).",
    )


def run_c500(context: FixtureContext, recorder: Recorder) -> None:
    config = context.config
    recorder.mark_rss("baseline")
    engine = harness.bootstrap_database(context.data_root)
    try:
        with session_factory(engine)() as session:
            seed_projects(
                session,
                seed=context.seed,
                fixture_name=context.spec.name,
                project_count=int(config["projects"]),
                chapters_per_project=int(config["chapters"]),
            )
        recorder.mark_rss("afterSeed")
        _measure_library_page(
            context,
            recorder,
            data_root=context.data_root,
            label="libraryMetadata500",
            limit=int(config["pageSize"]),
            sample_rss=True,
            note="GET /api/projects?limit=20 trên project 500 chương; RSS lấy mẫu sau mỗi lượt.",
        )
        recorder.mark_rss("afterMeasurement")
    finally:
        engine.dispose()

    rss = recorder.rss_report()
    recorder.set_measurement(
        "operation",
        "GET /api/projects?limit=20 cho project 500 chương; RSS đo cùng tiến trình so với baseline trước khi dựng fixture.",
    )
    recorder.set_phase("db", "MEASURED", metric="libraryMetadata500.db")
    recorder.set_phase("queue", "NOT_RUN", note="C500 không đi qua worker queue.")
    _set_provider_phases(recorder)
    recorder.threshold_le(
        case="C500 metadata RSS",
        target=256,
        measured=float(rss["delta"]),
        unit="MiB",
        label="tăng ≤ 256 MiB so với baseline cùng process",
        note=(
            "RSS peak trong tiến trình CLI trừ baseline đo trước khi dựng fixture "
            f"(baseline {rss['baseline']} MiB, peak {rss['peak']} MiB)."
        ),
    )


# --------------------------------------------------------------------------- #
# Fixture J10K — 10 client tranh chấp claim
# --------------------------------------------------------------------------- #


def run_j10k(context: FixtureContext, recorder: Recorder) -> None:
    config = context.config
    clients = int(config["clients"])
    jobs = int(config["jobs"])
    engine = harness.bootstrap_database(context.data_root)
    try:
        with session_factory(engine)() as session:
            project_ids = seed_projects(
                session,
                seed=context.seed,
                fixture_name=context.spec.name,
                project_count=1,
                chapters_per_project=0,
            )
        seed_jobs(engine, count=jobs, project_id=project_ids[0], fixture_name=context.spec.name)
        runner = JobRunner(engine)
        barrier = threading.Barrier(clients)
        results: list[dict[str, Any]] = []
        lock = threading.Lock()

        def client_loop(client_index: int) -> None:
            worker_id = f"worker-{client_index:02d}"
            total: list[float] = []
            db_ms: list[float] = []
            statements: list[float] = []
            leased: list[str] = []
            busy = 0
            empty = 0
            barrier.wait(timeout=120)
            for index in range(context.warmup + context.iterations):
                try:
                    with operation(context) as box:
                        lease = runner.claim(worker_id, datetime.now(UTC))
                        box["leased"] = 1.0 if lease is not None else 0.0
                except OperationalError as exc:
                    if _is_busy(exc):
                        busy += 1
                        continue
                    raise
                if lease is None:
                    empty += 1
                    continue
                leased.append(lease.job_id)
                if index < context.warmup:
                    continue
                total.append(box["elapsedMs"])
                db_ms.append(box["dbMs"])
                statements.append(box["statements"])
            with lock:
                results.append(
                    {
                        "workerId": worker_id,
                        "total": total,
                        "dbMs": db_ms,
                        "statements": statements,
                        "leased": leased,
                        "busy": busy,
                        "empty": empty,
                    }
                )

        threads = [threading.Thread(target=client_loop, args=(index,), name=f"client-{index}") for index in range(clients)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=600)
        if any(thread.is_alive() for thread in threads):
            raise FixtureError("J10K: client claim không kết thúc trong thời gian cho phép")

        total_all = [value for result in results for value in result["total"]]
        db_all = [value for result in results for value in result["dbMs"]]
        statements_all = [value for result in results for value in result["statements"]]
        leased_all = [job_id for result in results for job_id in result["leased"]]
        busy_total = sum(int(result["busy"]) for result in results)
        empty_total = sum(int(result["empty"]) for result in results)
        attempts = clients * (context.warmup + context.iterations)

        if not total_all:
            raise FixtureError("J10K: không claim được job nào (hàng đợi rỗng?)")

        duplicates_in_run = len(leased_all) - len(set(leased_all))
        with engine.connect() as connection:
            duplicate_committed = int(
                connection.execute(
                    text("SELECT COUNT(*) FROM (SELECT job_id FROM job_attempts GROUP BY job_id HAVING COUNT(*) > 1)")
                ).scalar_one()
            )
            running_jobs = int(connection.execute(text("SELECT COUNT(*) FROM jobs WHERE status = 'RUNNING'")).scalar_one())
            attempt_rows = int(connection.execute(text("SELECT COUNT(*) FROM job_attempts")).scalar_one())

        busy_rate = (busy_total / attempts) * 100.0 if attempts else 0.0
    finally:
        engine.dispose()

    recorder.record(
        "claim.total",
        total_all,
        "ms",
        note="Một lần claim của một client (BEGIN IMMEDIATE + chọn job QUEUED + tạo lease/attempt).",
    )
    recorder.record(
        "claim.db",
        db_all,
        "ms",
        note=(
            "Thời gian trong SQLite cho chính lần claim đó, quy kết thread-local: mỗi client chạy claim "
            "trên thread của nó nên số đo không lẫn SQL của client khác."
        ),
    )
    recorder.record(
        "claim.statements",
        statements_all,
        "statements",
        digits=STATEMENT_DIGITS,
        note="Số câu SQL cho một lần claim, đo trên thread của client (thread-local) nên vẫn chính xác khi chạy đồng thời.",
    )
    recorder.add_query("claim", statements_all)
    recorder.response_bytes_note = (
        "J10K đo worker claim nội bộ (không qua HTTP) nên không có bytes response; "
        "số liệu claim nằm ở extras.claims."
    )
    recorder.extras["claims"] = {
        "clients": clients,
        "queuedJobs": jobs,
        "attempts": attempts,
        "leased": len(leased_all),
        "empty": empty_total,
        "busy": busy_total,
        "busyRatePercent": round(busy_rate, 4),
        "duplicateLeasesInRun": duplicates_in_run,
        "duplicateCommittedClaims": duplicate_committed,
        "runningJobs": running_jobs,
        "attemptRows": attempt_rows,
    }
    recorder.set_measurement(
        "operation",
        f"{clients} client cùng tranh chấp claim trên hàng đợi {jobs} job; mỗi client "
        f"{context.warmup} warm-up + {context.iterations} lượt đo.",
    )
    recorder.set_phase(
        "queue",
        "MEASURED",
        metric="claim.total",
        note=(
            "Claim là độ trễ hàng đợi (chọn job QUEUED + tạo lease/attempt); 10 client chạy đồng thời "
            "qua threading.Barrier để tạo tranh chấp thật trên cùng một SQLite WAL."
        ),
    )
    recorder.set_phase("db", "MEASURED", metric="claim.db")
    _set_provider_phases(recorder)
    recorder.threshold_le(
        case="Claim 10 client cạnh tranh",
        target=150,
        measured=recorder.p("claim.total", 0.95),
        unit="ms",
        label="p95 ≤ 150 ms",
        note="G-PERF (plan §7).",
    )
    recorder.threshold_le(
        case="Claim 10 client cạnh tranh — busy",
        target=0.1,
        measured=round(busy_rate, 4),
        unit="%",
        label="busy ≤ 0,1%",
        note=f"{busy_total}/{attempts} lần claim trả SQLITE_BUSY (database is locked).",
    )
    recorder.threshold_bool(
        case="0 duplicate committed claim",
        ok=duplicate_committed == 0 and duplicates_in_run == 0,
        measured={"duplicateCommittedClaims": duplicate_committed, "duplicateLeasesInRun": duplicates_in_run},
        unit="duplicate",
        label="0 duplicate committed claim",
        target=0,
        note="Kiểm tra cả lease thu được trong lượt chạy lẫn bảng job_attempts sau khi commit.",
    )


def _is_busy(exc: OperationalError) -> bool:
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message or "busy" in message


# --------------------------------------------------------------------------- #
# Fixture SSE30 — replay 1k event sau cursor
# --------------------------------------------------------------------------- #


def run_sse30(context: FixtureContext, recorder: Recorder) -> None:
    config = context.config
    events = int(config["events"])
    cursor = int(config["cursor"])
    expected_replay = int(config["replayEvents"])
    engine = harness.bootstrap_database(context.data_root)
    try:
        with session_factory(engine)() as session:
            project_ids = seed_projects(
                session,
                seed=context.seed,
                fixture_name=context.spec.name,
                project_count=1,
                chapters_per_project=0,
            )
        seed_jobs(engine, count=events, project_id=project_ids[0], fixture_name=context.spec.name)

        total: list[float] = []
        db_ms: list[float] = []
        statements: list[float] = []
        payload_max: list[float] = []
        payload_total: list[float] = []
        counts: list[float] = []
        with session_factory(engine)() as session:
            primed = _events(session, after=None)
            if len(primed) < events:
                raise FixtureError(f"SSE30: chỉ backfill được {len(primed)}/{events} event")
            for index in range(context.warmup + context.iterations):
                with operation(context) as box:
                    replayed = _events(session, after=cursor)
                    payloads = [json.dumps(event, separators=(",", ":")) for event in replayed]
                    box["replayEvents"] = float(len(replayed))
                    box["payloadMax"] = float(max((len(payload.encode("utf-8")) for payload in payloads), default=0))
                    box["payloadTotal"] = float(sum(len(payload.encode("utf-8")) for payload in payloads))
                if index < context.warmup:
                    continue
                total.append(box["elapsedMs"])
                db_ms.append(box["dbMs"])
                statements.append(box["statements"])
                payload_max.append(box["payloadMax"])
                payload_total.append(box["payloadTotal"])
                counts.append(box["replayEvents"])
    finally:
        engine.dispose()

    recorder.record("sse.replay1000.total", total, "ms", note="Đọc event sau cursor + serialize JSON từng event (như route SSE).")
    recorder.record(
        "sse.replay1000.db",
        db_ms,
        "ms",
        note="Thời gian trong SQLite cho lượt replay (kể cả bước backfill event_log).",
    )
    recorder.record(
        "sse.replay1000.other",
        [total_value - db_value for total_value, db_value in zip(total, db_ms, strict=False)],
        "ms",
        note=(
            "Phần Python/ORM còn lại — đây là chỗ phình của replay hiện tại: _events() nạp toàn bộ "
            "event_log rồi session.get() từng dòng và lọc cursor bằng Python thay vì WHERE sequence_id > cursor."
        ),
    )
    recorder.record("sse.replay1000.events", counts, "events", digits=1)
    recorder.record("sse.replay1000.payloadBytesMax", payload_max, "bytes", digits=BYTE_DIGITS, note="Payload lớn nhất của một event trong lượt replay.")
    recorder.record("sse.replay1000.payloadBytesTotal", payload_total, "bytes", digits=BYTE_DIGITS)
    recorder.record("sse.replay1000.statements", statements, "statements", digits=STATEMENT_DIGITS)
    recorder.add_query("sse.replay1000", statements)
    recorder.add_response_bytes("sse.replay1000.payloadBytesMax", payload_max)
    recorder.response_bytes_note = (
        "Payload tính bằng UTF-8 bytes của json.dumps(event, separators=(',', ':')) — đúng cách route SSE dựng frame."
    )
    recorder.extras["sse"] = {
        "seededEvents": events,
        "cursor": cursor,
        "replayedEvents": int(counts[0]) if counts else 0,
        "maxEventPayloadBytes": int(max(payload_max)) if payload_max else 0,
        "backfilledEvents": len(primed),
    }
    recorder.set_measurement(
        "operation",
        f"Replay {expected_replay} event sau cursor {cursor} (backfill {events} job vào event_log rồi đọc lại).",
    )
    recorder.set_phase("db", "MEASURED", metric="sse.replay1000.db")
    recorder.set_phase("queue", "NOT_RUN", note="SSE30 chỉ đo replay log, không đo hàng đợi job.")
    _set_provider_phases(recorder)

    replayed = int(counts[0]) if counts else 0
    recorder.threshold_le(
        case="Replay 1k event sau cursor",
        target=300,
        measured=recorder.p("sse.replay1000.total", 0.95),
        unit="ms",
        label="p95 ≤ 300 ms",
        note="G-PERF (plan §7).",
    )
    recorder.threshold_bool(
        case="Replay 1k event sau cursor — số event",
        ok=replayed == expected_replay,
        measured=replayed,
        unit="events",
        label=f"= {expected_replay} event",
        target=expected_replay,
        note=f"Đọc sau cursor {cursor} từ {events} event đã backfill.",
    )
    recorder.threshold_le(
        case="Payload event",
        target=16384,
        measured=recorder.metric_max("sse.replay1000.payloadBytesMax"),
        unit="bytes",
        label="≤ 16 KiB",
        note="Payload lớn nhất trong mọi lượt đo, tính bằng UTF-8 bytes sau json.dumps như route SSE.",
    )


# --------------------------------------------------------------------------- #
# Fixture AUDIO500 — 500 segment audio (fake adapter)
# --------------------------------------------------------------------------- #


class _FaultInjectingWriter(RecoveryArtifactWriter):
    """Ghi checkpoint thật, nhưng cố tình lỗi một lần cho mỗi segment được chỉ định."""

    def __init__(self, session: Any, artifact_root: Path, *, fault_hashes: set[str]) -> None:
        super().__init__(session, artifact_root)
        self._fault_hashes = set(fault_hashes)
        self.injected_faults = 0

    def write_payload(self, write: Any, payload: bytes) -> Any:
        if write.input_hash in self._fault_hashes:
            self._fault_hashes.discard(write.input_hash)
            self.injected_faults += 1
            raise OSError("FAKE_FAULT_DURING_SEGMENT_WRITE")
        return super().write_payload(write, payload)


def _range_route_available() -> tuple[bool, str]:
    """Kiểm tra backend đã có route phục vụ Range/206 hay chưa (A05)."""

    from scripts.benchmarks import BACKEND_DIR

    app_dir = BACKEND_DIR / "app"
    if not app_dir.is_dir():
        return False, f"không thấy {app_dir}"
    for path in sorted(app_dir.rglob("*.py")):
        try:
            text_body = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if "content-range" in text_body or "accept-ranges" in text_body:
            return True, str(path)
    return False, "không file nào trong backend/app chứa 'Content-Range'/'Accept-Ranges'"


def _range_probe(
    recorder: Recorder,
    *,
    engine: Engine,
    data_root: Path,
    chapter_id: str,
    seed: int,
    payload_size: int,
    artifact_ids: dict[int, str],
    probes: int,
) -> dict[str, Any]:
    """Probe HTTP Range thật qua route A05 (`.../audio/artifacts/{id}/content`).

    Mỗi lượt gửi `Range: bytes=start-end` cho một artifact READY đã ghi ở lượt
    checkpoint và kiểm tra status 206, `Content-Range` đúng và byte trả về khớp
    chính xác payload gốc (seek Range thành công).
    """

    from app.api.audio import create_audio_router

    app = FastAPI()
    app.include_router(create_audio_router(Settings(data_root=data_root)))
    latencies: list[float] = []
    probe_rows: list[dict[str, Any]] = []
    selected = sorted(artifact_ids.items())[: max(1, probes)]
    if not selected:
        return {"ok": False, "reason": "không có artifact READY nào để probe Range", "probes": []}

    with engine.connect() as connection:
        sizes = {
            row[0]: int(row[1])
            for row in connection.execute(
                text("SELECT id, byte_size FROM artifacts WHERE status = 'READY' AND chapter_id = :chapter_id"),
                {"chapter_id": chapter_id},
            ).all()
        }

    with TestClient(app) as client:
        for segment_index, artifact_id in selected:
            byte_size = sizes.get(artifact_id)
            if byte_size is None:
                probe_rows.append({"artifactId": artifact_id, "ok": False, "reason": "artifact không còn READY"})
                continue
            payload = _fake_payload(seed, segment_index, payload_size)
            total = int(byte_size)
            start = total // 4
            end = min(total - 1, start + 255)
            started = time.perf_counter()
            response = client.get(
                f"/api/chapters/{chapter_id}/audio/artifacts/{artifact_id}/content",
                headers={"Range": f"bytes={start}-{end}"},
            )
            elapsed = (time.perf_counter() - started) * 1000.0
            body = response.content
            expected = payload[start : end + 1]
            ok = (
                response.status_code == 206
                and response.headers.get("content-range") == f"bytes {start}-{end}/{total}"
                and response.headers.get("accept-ranges") == "bytes"
                and body == expected
            )
            probe_rows.append(
                {
                    "artifactId": artifact_id,
                    "range": f"bytes={start}-{end}",
                    "status": response.status_code,
                    "contentRange": response.headers.get("content-range"),
                    "bytesReturned": len(body),
                    "segmentIndex": segment_index,
                    "bytesExact": body == expected,
                    "latencyMs": round(elapsed, 3),
                    "ok": ok,
                }
            )
            if ok:
                latencies.append(elapsed)

    all_ok = bool(probe_rows) and all(row["ok"] for row in probe_rows)
    if latencies:
        recorder.record(
            "audio.rangeSeek.total",
            latencies,
            "ms",
            note="Một lượt seek: HTTP GET có header Range qua route A05 (206 + slice đúng).",
        )
    return {
        "ok": all_ok,
        "probes": probe_rows,
        "route": "GET /api/chapters/{chapterId}/audio/artifacts/{artifactId}/content",
        "source": "backend/app/api/audio.py",
    }


def run_audio500(context: FixtureContext, recorder: Recorder) -> None:
    config = context.config
    segments = int(config["segments"])
    payload_size = int(config["payloadBytes"])
    kind = ArtifactKind(str(config["kind"]))
    artifact_root = context.data_root / "artifacts"

    engine = harness.bootstrap_database(context.data_root)
    try:
        with session_factory(engine)() as session:
            seed_projects(
                session,
                seed=context.seed,
                fixture_name=context.spec.name,
                project_count=1,
                chapters_per_project=1,
            )
        chapter_id = _chapter_id(0, 1)
        settings_hash = _sha256_text(f"{context.seed}:voice-plan:v1")
        inputs = {
            index: _sha256_text(f"{context.seed}:segment:{index}") for index in range(segments)
        }

        faults = set(inputs.values()) if context.fault_mode == "fail-first-attempt" else set()
        total: list[float] = []
        db_ms: list[float] = []
        statements: list[float] = []
        fake_provider_ms: list[float] = []
        artifact_ids: dict[int, str] = {}
        injected = 0
        created = 0

        with session_factory(engine)() as session:
            writer = _FaultInjectingWriter(session, artifact_root, fault_hashes=faults)
            for index in range(segments):
                latency = 0.0
                if context.fake_latency_ms > 0:
                    started_fake = time.perf_counter()
                    time.sleep(context.fake_latency_ms / 1000.0)
                    latency = (time.perf_counter() - started_fake) * 1000.0
                with operation(context) as box:
                    try:
                        checkpoint = writer.checkpoint_ready_artifact(
                            chapter_id=chapter_id,
                            kind=kind,
                            segment_id=f"segment-{index:04d}",
                            input_hash=inputs[index],
                            settings_hash=settings_hash,
                            payload=_fake_payload(context.seed, index, payload_size),
                            mime_type="audio/mpeg",
                            metadata={"fixture": "AUDIO500", "fake": True, "segmentIndex": index},
                            provider_sent=False,
                            usage_committed=False,
                        )
                        box["cacheHit"] = 1.0 if checkpoint.was_cache_hit else 0.0
                        box["artifactId"] = checkpoint.artifact_id
                    except OSError:
                        box["cacheHit"] = -1.0
                if box["cacheHit"] < 0:
                    injected += 1
                    continue
                artifact_ids[index] = str(box.get("artifactId", ""))
                created += 1 if box["cacheHit"] == 0 else 0
                total.append(box["elapsedMs"])
                db_ms.append(box["dbMs"])
                statements.append(box["statements"])
                if latency:
                    fake_provider_ms.append(latency)
            session.commit()

        # Lượt 2 (hồi phục): không tiêm lỗi. Ở chế độ fault, đây là lượt phải TẠO LẠI đúng các
        # segment đã lỗi ở lượt 1; ở chế độ sạch, đây là lượt trúng cache toàn bộ.
        recovery = _audio_checkpoint_pass(
            engine,
            artifact_root=artifact_root,
            kind=kind,
            chapter_id=chapter_id,
            settings_hash=settings_hash,
            inputs=inputs,
            seed=context.seed,
            payload_size=payload_size,
        )
        artifact_ids.update(recovery.pop("artifactIds"))

        # Lượt 3 (resume): phải trúng cache toàn bộ và không tạo artifact mới.
        resume = _audio_checkpoint_pass(
            engine,
            artifact_root=artifact_root,
            kind=kind,
            chapter_id=chapter_id,
            settings_hash=settings_hash,
            inputs=inputs,
            seed=context.seed,
            payload_size=payload_size,
        )
        artifact_ids.update(resume.pop("artifactIds"))

        duplicates, ready_total = _duplicate_ready_artifacts(engine, kind=kind)
        duplicate_rejected = _duplicate_ready_rejected(
            engine,
            artifact_root=artifact_root,
            kind=kind,
            chapter_id=chapter_id,
            settings_hash=settings_hash,
            input_hash=inputs[0],
            seed=context.seed,
            payload_size=payload_size,
        )
        range_available, range_evidence = _range_route_available()
        range_probe: dict[str, Any] | None = None
        if range_available:
            range_probe = _range_probe(
                recorder,
                engine=engine,
                data_root=context.data_root,
                chapter_id=chapter_id,
                seed=context.seed,
                payload_size=payload_size,
                artifact_ids=artifact_ids,
                probes=int(config.get("rangeProbes", 10)),
            )
    finally:
        engine.dispose()

    if total:
        recorder.record("audio.checkpoint.total", total, "ms", note="Một segment: cache lookup + ghi artifact + ghi DB.")
        recorder.record("audio.checkpoint.db", db_ms, "ms")
        recorder.record("audio.checkpoint.statements", statements, "statements", digits=STATEMENT_DIGITS)
        recorder.add_query("audio.checkpoint", statements)
    if fake_provider_ms:
        recorder.record(
            "audio.fakeSynthesis.total",
            fake_provider_ms,
            "ms",
            note="Latency GIẢ LẬP theo --fake-latency-ms; tách khỏi DB/API budget.",
        )
    recorder.add_response_bytes("audio.payloadBytes", [float(payload_size)] * len(total))
    recorder.response_bytes_note = (
        "Kích thước payload tổng hợp mỗi segment do fixture sinh (--fixture AUDIO500), không phải response HTTP."
    )

    recorder.extras["audio"] = {
        "segments": segments,
        "payloadBytesPerSegment": payload_size,
        "artifactKind": kind.value,
        "measuredSegments": len(total),
        "passes": {
            "firstPassWithFaultInjection": {"created": created, "injectedFaults": injected, "cacheHits": 0},
            "recoveryPass": recovery,
            "resumePass": resume,
        },
        "readyArtifacts": ready_total,
        "duplicateReadyArtifacts": duplicates,
        "duplicateReadyRejectedByIndex": duplicate_rejected,
        "injectedFaults": injected,
        "createdFirstPass": created,
        "persistedAfterRecovery": created + int(recovery.get("created", 0)),
        "fakeAdapter": True,
        "rangeRouteAvailable": range_available,
        "rangeRouteEvidence": range_evidence,
        "rangeProbe": range_probe,
    }
    recorder.set_measurement(
        "operation",
        (
            f"Ghi checkpoint cho {segments} segment (payload tổng hợp {payload_size} B/segment, "
            "RecoveryArtifactWriter thật + ArtifactStore thật), rồi chạy lại toàn bộ để kiểm tra resume."
        ),
    )
    recorder.note(
        "AUDIO500 dùng payload TỔNG HỢP (fake) — chỉ chứng minh plumbing/duplicate/resume; "
        "KHÔNG phải audio VieNeu thật và không claim chất lượng/TTS live."
    )
    if context.fault_mode == "fail-first-attempt":
        recorder.note(
            "Fault mode=fail-first-attempt: lượt 1 tiêm lỗi giữa lượt ghi mỗi segment (partial bị dọn, "
            "không có READY nửa vời) nên lượt 1 tạo 0 segment; lượt 2 (hồi phục, không tiêm lỗi) phải tạo "
            "lại đủ; lượt 3 (resume) phải trúng cache toàn bộ và không tạo artifact mới."
        )
    if not total:
        recorder.note(
            "Không có mẫu latency checkpoint nào ở lượt 1 (fault mode tiêm lỗi cho mọi segment), nên "
            "metrics audio.checkpoint.* không được ghi; độ trễ vẫn nằm trong extras.audio.passes."
        )
    if context.fake_latency_ms:
        recorder.note(
            f"Fake latency {context.fake_latency_ms} ms/segment được ghi ở pha provider (riêng), "
            "không cộng vào metric DB."
        )
    recorder.set_phase("db", "MEASURED", metric="audio.checkpoint.db")
    recorder.set_phase(
        "queue",
        "NOT_RUN",
        note="AUDIO500 gọi trực tiếp checkpoint của worker, không mô phỏng hàng đợi job.",
    )
    _set_provider_phases(recorder, fake_latency_metric="audio.fakeSynthesis.total" if fake_provider_ms else None)

    recorder.threshold_bool(
        case="Audio 500 segment — duplicate READY",
        ok=duplicates == 0,
        measured=duplicates,
        unit="duplicate",
        label="0 duplicate READY",
        target=0,
        note=f"{ready_total} artifact READY trong DB sau lượt ghi; kiểm tra theo (kind, input_hash, settings_hash).",
    )
    recovered_all = created + int(recovery["created"]) == segments
    resume_clean = resume["cacheHits"] == segments and resume["created"] == 0
    recorder.threshold_bool(
        case="Audio 500 segment — checkpoint/part-master resume",
        ok=recovered_all and resume_clean,
        measured={
            "firstPassCreated": created,
            "injectedFaults": injected,
            "recoveryCreated": recovery["created"],
            "recoveryCacheHits": recovery["cacheHits"],
            "resumeCacheHits": resume["cacheHits"],
            "resumeCreated": resume["created"],
            "segments": segments,
        },
        unit="segment",
        label="hồi phục đủ 500 segment rồi resume 0 artifact mới",
        target=segments,
        note=(
            "Lượt hồi phục (không tiêm lỗi) phải tạo đúng phần còn thiếu để đủ 500 segment; "
            "lượt resume sau đó phải trúng cache toàn bộ (sha256 + byte_size khớp file trên đĩa) và tạo 0 "
            f"artifact mới. Index unique READY từ chối bản trùng: {duplicate_rejected}."
        ),
    )
    if range_probe is not None and range_probe.get("ok"):
        probe_rows = range_probe["probes"]
        recorder.threshold_bool(
            case="Audio 500 segment — seek Range",
            ok=True,
            measured={
                "probes": len(probe_rows),
                "status206": sum(1 for row in probe_rows if row.get("status") == 206),
                "bytesExact": sum(1 for row in probe_rows if row.get("bytesExact")),
            },
            unit="http",
            label="seek Range thành công (206 + slice đúng)",
            target=len(probe_rows),
            note=(
                "Probe HTTP thật qua route A05: " + range_probe["route"] + " với header Range, "
                "kiểm tra status 206, Content-Range, Accept-Ranges và byte trả về khớp payload gốc."
            ),
        )
    else:
        reason = (
            "Probe HTTP Range thất bại: "
            + str((range_probe or {}).get("reason") or (range_probe or {}).get("probes"))
            if range_probe is not None
            else f"Backend chưa có route phục vụ HTTP Range/206 (A05 đang triển khai song song): {range_evidence}."
        )
        recorder.threshold_not_run(
            case="Audio 500 segment — seek Range",
            label="seek Range thành công",
            reason=reason + " Harness không tự dựng server giả để tránh claim sai.",
            unit="http",
        )


def _audio_checkpoint_pass(
    engine: Engine,
    *,
    artifact_root: Path,
    kind: ArtifactKind,
    chapter_id: str,
    settings_hash: str,
    inputs: dict[int, str],
    seed: int,
    payload_size: int,
) -> dict[str, Any]:
    """Chạy lại toàn bộ checkpoint KHÔNG tiêm lỗi (lượt hồi phục hoặc lượt resume).

    Mỗi segment phải hoặc trúng cache (file READY trên đĩa khớp sha256 + byte_size) hoặc được
    tạo mới; trả về số liệu để phân biệt "hồi phục sau lỗi" với "resume idempotent".
    """

    cache_hits = 0
    created = 0
    artifact_ids: dict[int, str] = {}
    with session_factory(engine)() as session:
        writer = RecoveryArtifactWriter(session, artifact_root)
        for index in range(len(inputs)):
            checkpoint = writer.checkpoint_ready_artifact(
                chapter_id=chapter_id,
                kind=kind,
                segment_id=f"segment-{index:04d}",
                input_hash=inputs[index],
                settings_hash=settings_hash,
                payload=_fake_payload(seed, index, payload_size),
                mime_type="audio/mpeg",
                metadata={"fixture": "AUDIO500", "fake": True, "segmentIndex": index},
                provider_sent=False,
                usage_committed=False,
            )
            if checkpoint.was_cache_hit:
                cache_hits += 1
            else:
                created += 1
            artifact_ids[index] = checkpoint.artifact_id
        session.commit()
    return {"cacheHits": cache_hits, "created": created, "artifactIds": artifact_ids}


def _duplicate_ready_artifacts(engine: Engine, *, kind: ArtifactKind) -> tuple[int, int]:
    with engine.connect() as connection:
        duplicates = int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM (SELECT kind, input_hash, settings_hash FROM artifacts "
                    "WHERE status = 'READY' AND kind = :kind GROUP BY kind, input_hash, settings_hash HAVING COUNT(*) > 1)"
                ),
                {"kind": kind.value},
            ).scalar_one()
        )
        ready_total = int(
            connection.execute(
                text("SELECT COUNT(*) FROM artifacts WHERE status = 'READY' AND kind = :kind"),
                {"kind": kind.value},
            ).scalar_one()
        )
    return duplicates, ready_total


def _duplicate_ready_rejected(
    engine: Engine,
    *,
    artifact_root: Path,
    kind: ArtifactKind,
    chapter_id: str,
    settings_hash: str,
    input_hash: str,
    seed: int,
    payload_size: int,
) -> bool:
    """Cố ghi thêm một READY trùng khóa: index unique của app phải từ chối."""

    payload = _fake_payload(seed, 0, payload_size)
    relative_path = f"audio/{chapter_id}/{kind.value.lower()}/duplicate-probe.mp3"
    target = artifact_root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO artifacts (id, chapter_id, kind, status, relative_path, sha256, byte_size, "
                    "mime_type, producer, producer_version, input_hash, settings_hash, metadata_json, "
                    "created_at, updated_at) VALUES (:id, :chapter_id, :kind, 'READY', :relative_path, "
                    ":sha256, :byte_size, 'audio/mpeg', 'benchmark', 'duplicate-probe', :input_hash, "
                    ":settings_hash, '{}', :now, :now)"
                ),
                {
                    "id": "018f0000-0000-7000-8000-d0000000000001",
                    "chapter_id": chapter_id,
                    "kind": kind.value,
                    "relative_path": relative_path,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "byte_size": len(payload),
                    "input_hash": input_hash,
                    "settings_hash": settings_hash,
                    "now": datetime.now(UTC).isoformat(),
                },
            )
    except IntegrityError:
        return True
    finally:
        target.unlink(missing_ok=True)
    return False


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


S1 = FixtureSpec(
    name="S1",
    title="Trang đầu 25 chương, DB lạnh (cold)",
    config={
        "projects": 1,
        "chapters": 40,
        "pageSize": 25,
        "coldDb": True,
        "sourceRevisions": 0,
        "dataProfile": "metadata-only",
    },
    runner=run_s1,
)

S2 = FixtureSpec(
    name="S2",
    title="Trang 25 chương ở giữa project 10k chương",
    config={
        "projects": 1,
        "chapters": 10_000,
        "pageSize": 25,
        "startOrdinal": 5_001,
        "coldDb": True,
        "sourceRevisions": 0,
        "dataProfile": "metadata-only",
    },
    runner=run_s2,
)

C2K = FixtureSpec(
    name="C2K",
    title="Metadata thư viện cho project 1k/10k chương",
    config={
        "projects": 1,
        "pageSize": 20,
        "chapterCounts": [1_000, 10_000],
        "dataProfile": "metadata-only",
    },
    runner=run_c2k,
)

C500 = FixtureSpec(
    name="C500",
    title="Metadata 500 chương và RSS cùng tiến trình",
    config={
        "projects": 1,
        "chapters": 500,
        "pageSize": 20,
        "dataProfile": "metadata-only",
    },
    runner=run_c500,
)

J10K = FixtureSpec(
    name="J10K",
    title="10 client tranh chấp claim trên 10k job",
    config={
        "projects": 1,
        "jobs": 10_000,
        "clients": 10,
        "priority": 100,
        "dataProfile": "queue",
    },
    runner=run_j10k,
)

SSE30 = FixtureSpec(
    name="SSE30",
    title="Replay 1k event sau cursor",
    config={
        "projects": 1,
        "events": 1_200,
        "cursor": 200,
        "replayEvents": 1_000,
        "dataProfile": "events",
    },
    runner=run_sse30,
)

AUDIO500 = FixtureSpec(
    name="AUDIO500",
    title="500 segment audio (fake adapter) — duplicate/resume/Range",
    config={
        "projects": 1,
        "segments": 500,
        "payloadBytes": 1_024,
        "kind": ArtifactKind.TTS_SEGMENT.value,
        "fakeAdapter": True,
        "rangeProbes": 10,
        "dataProfile": "audio-fake",
    },
    runner=run_audio500,
)

FIXTURES: dict[str, FixtureSpec] = {
    spec.name: spec for spec in (S1, S2, C2K, C500, J10K, SSE30, AUDIO500)
}


def fixture_names() -> list[str]:
    return list(FIXTURES)
