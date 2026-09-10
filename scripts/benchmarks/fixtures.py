"""Fixture và phép đo cho harness benchmark offline (task V01).

Mỗi fixture dựng dữ liệu bằng chính ORM/migration của app trên data root TẠM và đo
bằng code thật của app (router FastAPI thật, JobRunner thật, RecoveryArtifactWriter
thật). Không fixture nào gọi cloud, tải model hay chạm data root thật của studio.

Phân tách pha (theo rủi ro "đo lẫn provider latency" trong plan §7): mọi metric DB
được đo bằng listener SQLAlchemy before/after_cursor_execute, nên thời gian
provider/FFmpeg (nếu có) không bao giờ nằm trong budget API/DB.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
import json
import random
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.drafts import create_drafts_router
from app.api.events import _events
from app.api.jobs import create_jobs_router
from app.api.projects import create_projects_router
from app.api.translation import create_translation_router
from app.api.workspace import create_workspace_router
from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ChapterState,
    ImportKind,
    JobKind,
    JobStatus,
    RightsStatus,
    RunStatus,
    SourceType,
    VoiceOrigin,
)
from app.db.base import session_factory
from app.db.models import (
    Artifact,
    Chapter,
    Job,
    Project,
    SourceRevision,
    SourceSegment,
    TranslationRun,
    TranslationSegment,
    VoicePreset,
)
from app.modules.jobs.execution_handlers import build_synthesize_handler
from app.modules.jobs.recovery import RecoveryArtifactWriter
from app.modules.jobs.runner import JobRunner
from app.modules.projects.queries import ChapterQueries
from app.modules.speech.workflow import SpeechWorkflow
from app.modules.storage.backup import BackupService, BackupVerificationError
from app.providers.fake import FakeMp3AudioProcessor, FakeTts
from app.settings.config import Settings
from app.worker import Worker

from scripts.benchmarks import REPO_ROOT, harness
from scripts.benchmarks.stats import percentile, summarize

__all__ = ["FIXTURES", "FixtureContext", "FixtureSpec", "Recorder", "fixture_names"]

DEFAULT_SEED = 20260908
CURSOR_SECRET = "benchmark-cursor-secret"
MAX_MOUNTED_ROWS = 100
"""Trần số dòng mount một trang của app (projects/queries.py: MAX_CHAPTER_LIMIT = MAX_PROJECT_LIMIT = 100)."""
MAX_MOUNTED_TABS_PER_PANE = 8
"""Trần tab mỗi pane của workspace (modules/workspace/layout.py: MAX_TABS_PER_PANE = 8)."""
FRONTEND_DRIVERS = REPO_ROOT / "scripts" / "benchmarks" / "frontend"
"""Driver Node chạy CHÍNH module frontend (reducer 8 tab, job store) — chỉ đọc, không build."""
NODE_DRIVER_TIMEOUT_SECONDS = 300
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
    scale: float = 1.0
    session_seconds: float = 0.0

    def scaled(self, value: int, *, floor: int = 1) -> int:
        """Kích thước dữ liệu cho fixture V02 (--scale, mặc định 1.0 = đúng cấu hình fixture).

        Chỉ fixture V02 dùng; các fixture V01 giữ nguyên kích thước cố định.
        """

        return max(int(floor), int(round(int(value) * float(self.scale))))

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
# V02 — helper dùng chung cho các fixture G-PERF còn thiếu
# --------------------------------------------------------------------------- #


def _bench_id(prefix: str, index: int) -> str:
    """Id 36 ký tự đúng dạng UUID cho dữ liệu benchmark (prefix là một ký tự hex)."""

    return f"018f0000-0000-7000-8000-{prefix}{index:011d}"


def seed_translated_chapters(
    session: Any,
    *,
    project_index: int,
    ordinals: list[int],
    segments_per_chapter: int,
    seed: int,
) -> list[dict[str, object]]:
    """Revision nguồn + segment + translation run APPROVED cho từng chương đã seed.

    Dùng chính bảng của app (không phải payload giả): các route đọc chương
    (/api/chapters/{id}/translation, /api/chapters/{id}/draft) chạy trên dữ liệu này.
    """

    created = BASE_TIME + timedelta(minutes=1)
    records: list[dict[str, object]] = []
    counter = 0
    for position, ordinal in enumerate(ordinals):
        chapter_id = _chapter_id(project_index, ordinal)
        chapter = session.get(Chapter, chapter_id)
        if chapter is None:
            raise FixtureError(f"chương {chapter_id} chưa được seed")
        revision_text = f"source chuong {ordinal}"
        revision = SourceRevision(
            id=_bench_id("1", position),
            chapter_id=chapter_id,
            revision_no=1,
            import_kind=ImportKind.PASTE.value,
            normalized_text=revision_text,
            normalized_sha256=_sha256_text(f"{seed}:revision:{ordinal}"),
            han_char_count=0,
            total_char_count=len(revision_text),
            normalizer_version="nfc-v1",
            created_at=created,
            updated_at=created,
        )
        session.add(revision)
        session.flush()
        run = TranslationRun(
            id=_bench_id("3", position),
            chapter_id=chapter_id,
            source_revision_id=revision.id,
            prompt_version="translation-v1",
            status=RunStatus.APPROVED.value,
            translation_text_sha256=_sha256_text(f"{seed}:run:{ordinal}"),
            estimated_cost_vnd=0,
            actual_cost_vnd=0,
            created_at=created,
            updated_at=created,
        )
        session.add(run)
        session.flush()
        source_segment_ids: list[str] = []
        translation_segment_ids: list[str] = []
        for index in range(segments_per_chapter):
            target_text = (
                f"Cau {index + 1} cua chuong {ordinal} giu nhip ke chuyen cham rai, ro rang va mach lac."
            )
            source_segment_id = _bench_id("2", counter)
            translation_segment_id = _bench_id("4", counter)
            counter += 1
            session.add(
                SourceSegment(
                    id=source_segment_id,
                    source_revision_id=revision.id,
                    segment_index=index,
                    paragraph_start=index,
                    paragraph_end=index,
                    source_text=f"source {ordinal} {index}",
                    source_sha256=_sha256_text(f"{seed}:source:{ordinal}:{index}"),
                    segment_kind="SOURCE",
                    created_at=created,
                    updated_at=created,
                )
            )
            session.add(
                TranslationSegment(
                    id=translation_segment_id,
                    translation_run_id=run.id,
                    source_segment_id=source_segment_id,
                    target_text=target_text,
                    target_sha256=_sha256_text(target_text),
                    was_cache_hit=False,
                    manually_edited=False,
                    created_at=created,
                    updated_at=created,
                )
            )
            source_segment_ids.append(source_segment_id)
            translation_segment_ids.append(translation_segment_id)
        chapter.active_source_revision_id = revision.id
        chapter.approved_translation_run_id = run.id
        chapter.state = ChapterState.TRANSLATION_APPROVED.value
        records.append(
            {
                "ordinal": ordinal,
                "chapterId": chapter_id,
                "revisionId": revision.id,
                "runId": run.id,
                "sourceSegmentIds": source_segment_ids,
                "translationSegmentIds": translation_segment_ids,
            }
        )
    session.commit()
    return records


def seed_voice_preset(session: Any) -> VoicePreset:
    """Preset narrator + artifact license hợp lệ (đúng ràng buộc kiểm tra của bảng)."""

    created = BASE_TIME + timedelta(minutes=2)
    license_artifact = Artifact(
        id=_bench_id("5", 0),
        kind=ArtifactKind.LICENSE_SNAPSHOT.value,
        status=ArtifactStatus.READY.value,
        relative_path="models/benchmark-license.txt",
        sha256=_sha256_text("benchmark-license"),
        byte_size=17,
        mime_type="text/plain",
        input_hash=_sha256_text("benchmark-license-input"),
        settings_hash=_sha256_text("benchmark-license-settings"),
        created_at=created,
        updated_at=created,
    )
    preset = VoicePreset(
        id=_bench_id("5", 1),
        name="Narrator benchmark",
        provider_voice_id="voice-benchmark",
        locale="vi-VN",
        origin=VoiceOrigin.BUILT_IN.value,
        gender_label="neutral",
        region_label="local",
        speed="1.0",
        pitch="0",
        style=None,
        sample_rate=44_100,
        settings_json={"speed": "1.0", "pitch": "0"},
        model_snapshot_hash=_sha256_text("benchmark-model"),
        license_snapshot_artifact_id=license_artifact.id,
        active=True,
        created_at=created,
        updated_at=created,
    )
    session.add_all((license_artifact, preset))
    session.commit()
    return preset


def seed_artifact_files(
    engine: Engine,
    *,
    artifact_root: Path,
    project_index: int,
    count: int,
    payload_bytes: int,
    seed: int,
) -> int:
    """Ghi file artifact thật + hàng artifacts READY (đủ sha256/byte_size) cho fixture backup."""

    artifact_root.mkdir(parents=True, exist_ok=True)
    created = BASE_TIME + timedelta(minutes=3)
    chapter_id = _chapter_id(project_index, 1)
    with engine.begin() as connection:
        for index in range(count):
            payload = _fake_payload(seed, index, payload_bytes)
            relative_path = f"benchmark/{index:05d}.mp3"
            target = artifact_root / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            connection.execute(
                insert(Artifact),
                [
                    {
                        "id": _bench_id("6", index),
                        "chapter_id": chapter_id,
                        "kind": ArtifactKind.TTS_SEGMENT.value,
                        "status": ArtifactStatus.READY.value,
                        "relative_path": relative_path,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "byte_size": len(payload),
                        "mime_type": "audio/mpeg",
                        "producer": "benchmark",
                        "producer_version": "v02",
                        "input_hash": _sha256_text(f"{seed}:artifact-input:{index}"),
                        "settings_hash": _sha256_text(f"{seed}:artifact-settings"),
                        "metadata_json": {"fixture": "BACKUP", "index": index},
                        "created_at": created,
                        "updated_at": created,
                    }
                ],
            )
    return count


def _measure_endpoint(
    context: FixtureContext,
    recorder: Recorder,
    client: TestClient,
    *,
    url: str,
    label: str,
    note: str | None = None,
    rows_key: str | None = "items",
    method: str = "GET",
    json_body: dict[str, object] | None = None,
) -> dict[str, object]:
    """Đo một endpoint thật qua TestClient: tổng/DB/SQL/bytes + số dòng trả về."""

    total: list[float] = []
    db_ms: list[float] = []
    statements: list[float] = []
    payloads: list[float] = []
    attributions: list[str] = []
    rows_max = 0
    statuses: list[int] = []
    for index in range(context.warmup + context.iterations):
        with operation(context) as box:
            if method == "PUT":
                response = client.put(url, json=json_body)
            else:
                response = client.get(url)
            box["bytes"] = float(len(response.content))
        statuses.append(response.status_code)
        body: object = None
        try:
            body = response.json()
        except ValueError:
            body = None
        rows = 0
        if rows_key and isinstance(body, dict):
            value = body.get(rows_key)
            if isinstance(value, list):
                rows = len(value)
        rows_max = max(rows_max, rows)
        if response.status_code >= 400:
            raise FixtureError(f"{label}: {method} {url} trả về HTTP {response.status_code}: {response.text[:200]}")
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
    return {
        "rowsMax": rows_max,
        "statuses": sorted(set(statuses)),
        "payloadMaxBytes": int(max(payloads)) if payloads else 0,
    }


def _run_node_driver(
    driver: str,
    *,
    arguments: list[str] | None = None,
    expose_gc: bool = False,
) -> dict[str, object]:
    """Chạy driver Node trong scripts/benchmarks/frontend và đọc JSON cuối stdout."""

    node = shutil.which("node")
    if node is None:
        raise FixtureError("NODE_NOT_AVAILABLE")
    path = FRONTEND_DRIVERS / driver
    if not path.is_file():
        raise FixtureError(f"driver frontend không tồn tại: {path}")
    command = [node]
    if expose_gc:
        command.append("--expose-gc")
    command.append(str(path))
    command.extend(arguments or [])
    completed = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=NODE_DRIVER_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise FixtureError(
            f"driver {driver} thoát {completed.returncode}: {(completed.stderr or completed.stdout)[:400]}"
        )
    lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    if not lines:
        raise FixtureError(f"driver {driver} không in JSON")
    try:
        parsed = json.loads(lines[-1])
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise FixtureError(f"driver {driver} in JSON không hợp lệ: {lines[-1][:200]}") from exc
    if not isinstance(parsed, dict):
        raise FixtureError(f"driver {driver} phải in một object JSON")
    return parsed


def _node_driver_report(
    recorder: Recorder,
    *,
    driver: str,
    key: str,
    arguments: list[str] | None = None,
    expose_gc: bool = False,
    not_run_note: str,
) -> dict[str, object] | None:
    """Chạy driver frontend nếu có Node; nếu không thì ghi NOT_RUN và trả None."""

    try:
        result = _run_node_driver(driver, arguments=arguments, expose_gc=expose_gc)
    except FixtureError as exc:
        recorder.note(f"Không chạy được driver frontend {driver}: {exc}")
        recorder.extras[key] = {"ran": False, "reason": str(exc)}
        recorder.threshold_not_run(case=key, label=driver, reason=not_run_note)
        return None
    recorder.extras[key] = {"ran": True, **result}
    return result


def _app_with_routers(
    data_root: Path,
    *builders: Callable[..., Any],
    cursor_secret: str = CURSOR_SECRET,
) -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    settings = Settings(data_root=data_root)
    for builder in builders:
        if builder is create_projects_router:
            app.include_router(builder(settings, cursor_secret=cursor_secret))
        else:
            app.include_router(builder(settings))
    return app, TestClient(app)




# --------------------------------------------------------------------------- #
# Fixture CHLIST — chapter list/filter: ≤100 mounted rows + server filter/paging
# --------------------------------------------------------------------------- #


def run_chlist(context: FixtureContext, recorder: Recorder) -> None:
    config = context.config
    chapters = context.scaled(int(config["chapters"]))
    marker_chapters = context.scaled(int(config["markerChapters"]))
    page_size = int(config["pageSize"])
    clamp_limit = int(config["clampProbeLimit"])
    marker = str(config["marker"])
    pages_to_walk = min(int(config["pages"]), max(1, chapters // page_size))
    engine = harness.bootstrap_database(context.data_root)
    try:
        with session_factory(engine)() as session:
            project_ids = seed_projects(
                session,
                seed=context.seed,
                fixture_name=context.spec.name,
                project_count=1,
                chapters_per_project=chapters,
            )
        project_id = project_ids[0]
        with engine.begin() as connection:
            marked = connection.execute(
                text("SELECT id FROM chapters WHERE project_id = :project ORDER BY ordinal LIMIT :count"),
                {"project": project_id, "count": marker_chapters},
            ).all()
            for (chapter_id,) in marked:
                connection.execute(
                    text("UPDATE chapters SET source_title = :title WHERE id = :id"),
                    {"title": f"{marker} chương đánh dấu", "id": chapter_id},
                )
        marked_ids = [row[0] for row in marked]

        app, client = _app_with_routers(context.data_root, create_projects_router)
        with client:
            page = _measure_endpoint(
                context,
                recorder,
                client,
                url=f"/api/projects/{project_id}/chapters?limit={page_size}",
                label="chapterList100",
                note=(
                    f"GET /api/projects/{{id}}/chapters?limit={page_size} (trang mount của editor) "
                    "trên DB tạm; đo cả request + phần DB + bytes."
                ),
            )
            clamp = _measure_endpoint(
                context,
                recorder,
                client,
                url=f"/api/projects/{project_id}/chapters?limit={clamp_limit}",
                label="chapterListClampProbe",
                note=(
                    f"Probe trần: yêu cầu limit={clamp_limit} (lớn hơn MAX_CHAPTER_LIMIT) — server phải tự kẹp, "
                    "không được trả quá 100 dòng."
                ),
            )
            walk = _walk_chapter_pages(
                context,
                recorder,
                client,
                project_id=project_id,
                limit=page_size,
                pages=pages_to_walk,
            )
            filter_probe = _chapter_filter_probe(
                client,
                recorder,
                project_id=project_id,
                marker=marker,
                expected_matches=len(marked_ids),
                limit=page_size,
            )
    finally:
        engine.dispose()

    walk_rows = walk["rowsPerPage"]
    mounted_rows_max = max(page["rowsMax"], clamp["rowsMax"], *walk_rows, filter_probe["rowsReturned"])
    paging_ok = walk["coverageComplete"] and walk["noDuplicates"] and max(walk_rows) <= MAX_MOUNTED_ROWS
    server_filtered = bool(filter_probe["serverFiltered"])

    recorder.extras["chapterList"] = {
        "seededChapters": chapters,
        "pageSize": page_size,
        "clampProbeLimit": clamp_limit,
        "clampProbeRows": clamp["rowsMax"],
        "rowsPerPageMax": max(walk_rows) if walk_rows else 0,
        "pagesWalked": walk["pagesWalked"],
        "chaptersCovered": len(walk["ordinals"]),
        "coverageComplete": walk["coverageComplete"],
        "noDuplicates": walk["noDuplicates"],
        "filterProbe": filter_probe,
        "mountedRowsMax": mounted_rows_max,
    }
    recorder.set_measurement(
        "operation",
        (
            f"GET /api/projects/{{id}}/chapters thật trên project {chapters} chương: trang limit={page_size}, "
            f"probe trần limit={clamp_limit}, đi {walk['pagesWalked']} trang bằng cursor và probe filter q="
            f"{marker!r} (khớp {len(marked_ids)} chương)."
        ),
    )
    recorder.note(
        "'Mounted rows' đo bằng số dòng server trả về mỗi trang (proxy của số dòng UI mount): "
        "editor chỉ mount những gì trang trả về, và trần của app là MAX_CHAPTER_LIMIT = 100."
    )
    recorder.threshold_le(
        case="Chapter list/filter — dòng mount mỗi trang",
        target=MAX_MOUNTED_ROWS,
        measured=float(mounted_rows_max),
        unit="rows",
        label="≤100 mounted rows",
        note=(
            f"Trang limit={page_size} trả {page['rowsMax']} dòng; probe trần limit={clamp_limit} trả "
            f"{clamp['rowsMax']} dòng (server tự kẹp). Số dòng lớn nhất quan sát được: {mounted_rows_max}."
        ),
    )
    recorder.threshold_bool(
        case="Chapter list/filter — filter chạy server-side",
        ok=server_filtered,
        measured={
            "query": marker,
            "expectedMatches": len(marked_ids),
            "rowsReturned": filter_probe["rowsReturned"],
            "serverTotal": filter_probe["serverTotal"],
            "unfilteredTotal": chapters,
            "rowsMatchingQuery": filter_probe["rowsMatching"],
            "filterParamRows": filter_probe["filterParamEchoed"],
            "statusParamTotal": filter_probe["statusParamTotal"],
        },
        unit="http",
        label="filter/paging chạy ở server (q lọc trước khi trả trang)",
        target="server-side filter",
        note=(
            "GET /api/projects/{id}/chapters KHÔNG có tham số lọc: q bị bỏ qua và total vẫn là toàn bộ "
            f"{chapters} chương, nên client phải tự lọc trong trang đã mount ({page['rowsMax']} dòng) — "
            "không đáp ứng 'server filter'. Xem extras.chapterList.filterProbe để có bằng chứng nguyên văn."
        ),
    )
    recorder.threshold_bool(
        case="Chapter list/filter — paging phủ hết project",
        ok=paging_ok,
        measured={
            "pagesWalked": walk["pagesWalked"],
            "rowsPerPageMax": max(walk_rows) if walk_rows else 0,
            "chaptersCovered": len(walk["ordinals"]),
            "coverageComplete": walk["coverageComplete"],
            "noDuplicates": walk["noDuplicates"],
        },
        unit="cursor",
        label=f"đi hết {chapters} chương bằng cursor, mỗi trang ≤100 dòng, không trùng/thiếu",
        target=chapters,
        note=(
            "Cursor do chính route sinh (nextCursor) và ký HMAC; walk kiểm tra ordinal tăng dần, không "
            "trùng và không thiếu khi tới trang cuối."
        ),
    )
    recorder.set_phase("db", "MEASURED", metric="chapterList100.db")
    recorder.set_phase("queue", "NOT_RUN", note="CHLIST chỉ đọc metadata chương, không đi qua worker queue.")
    _set_provider_phases(recorder)


def _walk_chapter_pages(
    context: FixtureContext,
    recorder: Recorder,
    client: TestClient,
    *,
    project_id: str,
    limit: int,
    pages: int,
) -> dict[str, Any]:
    """Đi liên tiếp các trang bằng cursor thật của route, đo từng trang."""

    ordinals: list[int] = []
    rows_per_page: list[int] = []
    total: list[float] = []
    db_ms: list[float] = []
    statements: list[float] = []
    payloads: list[float] = []
    attributions: list[str] = []
    cursor: str | None = None
    pages_walked = 0
    for _ in range(pages):
        url = f"/api/projects/{project_id}/chapters?limit={limit}"
        if cursor is not None:
            url = f"{url}&cursor={quote(cursor)}"
        with operation(context) as box:
            response = client.get(url)
            box["bytes"] = float(len(response.content))
        if response.status_code != 200:
            raise FixtureError(f"walk trang chương: HTTP {response.status_code} {response.text[:200]}")
        body = response.json()
        items = body.get("items") or []
        rows_per_page.append(len(items))
        ordinals.extend(int(item["ordinal"]) for item in items)
        total.append(box["elapsedMs"])
        db_ms.append(box["dbMs"])
        statements.append(box["statements"])
        payloads.append(box["bytes"])
        attributions.append(str(box["dbAttribution"]))
        pages_walked += 1
        cursor = body.get("nextCursor")
        if not cursor or not items:
            break
    _record_api_metrics(
        recorder,
        label="chapterListCursorPage",
        total=total,
        db_ms=db_ms,
        statements=statements,
        payloads=payloads,
        attributions=attributions,
        note="Một trang 100 chương đi bằng nextCursor do route sinh (paging server-side).",
    )
    ordered = sorted(ordinals)
    return {
        "ordinals": ordinals,
        "rowsPerPage": rows_per_page,
        "pagesWalked": pages_walked,
        "noDuplicates": len(ordinals) == len(set(ordinals)),
        "coverageComplete": bool(ordinals)
        and ordered[0] == 1
        and len(ordered) == len(set(ordered))
        and ordered[-1] == len(ordered),
    }


def _chapter_filter_probe(
    client: TestClient,
    recorder: Recorder,
    *,
    project_id: str,
    marker: str,
    expected_matches: int,
    limit: int,
) -> dict[str, Any]:
    """Probe filter server-side của route danh sách chương (tham số q và filter)."""

    url = f"/api/projects/{project_id}/chapters?limit={limit}&q={quote(marker)}"
    started = time.perf_counter()
    response = client.get(url)
    elapsed = (time.perf_counter() - started) * 1000.0
    if response.status_code != 200:
        raise FixtureError(f"probe filter: HTTP {response.status_code} {response.text[:200]}")
    body = response.json()
    items = body.get("items") or []
    rows_matching = sum(1 for item in items if marker in str(item.get("sourceTitle") or ""))
    server_total = int(body.get("total") or 0)

    filter_probe = client.get(f"/api/projects/{project_id}/chapters?limit={limit}&filter={quote(marker)}")
    filter_rows = len(filter_probe.json().get("items") or []) if filter_probe.status_code == 200 else 0

    filtered_rows = client.get(f"/api/projects/{project_id}/chapters?limit={limit}&status=TRANSLATION_APPROVED")
    filtered_total = int(filtered_rows.json().get("total") or 0) if filtered_rows.status_code == 200 else -1

    server_filtered = server_total == expected_matches and len(items) == expected_matches and rows_matching == len(items)
    recorder.record(
        "chapterListFilterProbe.total",
        [elapsed],
        "ms",
        note="Một request có q (tham số lọc theo quy ước của app) — đo để biết chi phí, không phải ngưỡng G-PERF.",
    )
    return {
        "url": url,
        "query": marker,
        "expectedMatches": expected_matches,
        "rowsReturned": len(items),
        "rowsMatching": rows_matching,
        "serverTotal": server_total,
        "serverFiltered": server_filtered,
        "filterParamEchoed": filter_rows,
        "statusParamTotal": filtered_total,
        "probeLatencyMs": round(elapsed, 3),
    }




# --------------------------------------------------------------------------- #
# Fixture CHSWITCH — đổi chương cached: p95 <200 ms qua 30 lượt, tách cold/warm
# --------------------------------------------------------------------------- #


def run_chswitch(context: FixtureContext, recorder: Recorder) -> None:
    config = context.config
    chapters = context.scaled(int(config["chapters"]))
    content_chapters = min(chapters, context.scaled(int(config["contentChapters"])))
    segments_per_chapter = context.scaled(int(config["segmentsPerChapter"]), floor=2)
    switches = int(config["switches"])
    engine = harness.bootstrap_database(context.data_root)
    try:
        with session_factory(engine)() as session:
            project_ids = seed_projects(
                session,
                seed=context.seed,
                fixture_name=context.spec.name,
                project_count=1,
                chapters_per_project=chapters,
            )
            records = seed_translated_chapters(
                session,
                project_index=0,
                ordinals=list(range(1, content_chapters + 1)),
                segments_per_chapter=segments_per_chapter,
                seed=context.seed,
            )
        project_id = project_ids[0]

        app, client = _app_with_routers(context.data_root, create_translation_router, create_drafts_router)
        draft_revisions: dict[str, int] = {}
        with client:
            # Draft thật cho các chương nằm trong "cache" của editor (ghi qua route thật).
            for record in records:
                body = {
                    "base_revision_id": record["revisionId"],
                    "content": {
                        str(segment_id): f"Nhap {record['ordinal']} doan {index}"
                        for index, segment_id in enumerate(record["sourceSegmentIds"])
                    },
                    "expected_revision": None,
                }
                response = client.put(f"/api/chapters/{record['chapterId']}/draft", json=body)
                if response.status_code != 200:
                    raise FixtureError(f"PUT draft: HTTP {response.status_code} {response.text[:200]}")
                draft_revisions[str(record["chapterId"])] = int(response.json()["draft"]["revision"])

            cold = _measure_switches(
                context,
                recorder,
                client,
                records=records[: min(switches, len(records))],
                label="switchCold",
                note=(
                    "Lượt ĐẦU tới từng chương (cache nguội: DB/page cache chưa có gì cho chương đó) — "
                    "báo riêng, không dùng cho ngưỡng p95 cached."
                ),
            )
            warm = _measure_switches(
                context,
                recorder,
                client,
                records=[records[index % len(records)] for index in range(switches)],
                label="switchWarm",
                note=(
                    "Đổi chương ĐÃ CACHED: 30 lượt quay vòng qua các chương vừa mở (page cache ấm, draft "
                    "đã nằm trong store), đo cả request liên hợp + phần fetch (GET translation) riêng."
                ),
            )
    finally:
        engine.dispose()

    fetch_p95 = recorder.p("switchWarm.fetch.total", 0.95)
    combined_p95 = recorder.p("switchWarm.total", 0.95)
    recorder.extras["chapterSwitch"] = {
        "projectId": project_id,
        "switches": switches,
        "chaptersInProject": chapters,
        "chaptersWithContent": content_chapters,
        "segmentsPerChapter": segments_per_chapter,
        "draftRevisions": draft_revisions,
        "warm": warm,
        "cold": cold,
        "warmP95Ms": round(combined_p95, 3),
        "fetchP95Ms": round(fetch_p95, 3),
    }
    recorder.set_measurement(
        "operation",
        (
            "Một lượt đổi chương = GET /api/chapters/{id}/translation (payload chương, phần fetch) + "
            "GET /api/chapters/{id}/draft (khôi phục nháp của editor), cả hai qua router thật trên data root tạm."
        ),
    )
    recorder.note(
        "Ngưỡng G-PERF đặt trên lượt ĐỔI CHƯƠNG ĐÃ CACHED (warm). Thời gian fetch được báo riêng ở metric "
        "switchWarm.fetch.total (p95) và switchWarm.db — không cộng lẫn provider (không có provider nào)."
    )
    recorder.set_phase("db", "MEASURED", metric="switchWarm.db")
    recorder.set_phase("queue", "NOT_RUN", note="CHSWITCH chỉ đọc chương/nháp, không đi qua worker queue.")
    _set_provider_phases(recorder)
    recorder.threshold_le(
        case="Đổi chương cached",
        target=200,
        measured=combined_p95,
        unit="ms",
        label="p95 < 200 ms qua 30 lượt",
        note=(
            f"p95 lượt đổi chương warm = {combined_p95:.3f} ms; riêng phần fetch "
            f"(GET translation) p95 = {fetch_p95:.3f} ms. Lượt cold p95 = "
            f"{recorder.p('switchCold.total', 0.95):.3f} ms (báo riêng, không dùng cho ngưỡng này)."
        ),
    )


def _measure_switches(
    context: FixtureContext,
    recorder: Recorder,
    client: TestClient,
    *,
    records: list[dict[str, object]],
    label: str,
    note: str,
) -> dict[str, Any]:
    """Đo một lượt đổi chương (fetch payload chương + khôi phục nháp) trên route thật."""

    combined: list[float] = []
    fetch_total: list[float] = []
    fetch_db: list[float] = []
    draft_total: list[float] = []
    draft_db: list[float] = []
    statements: list[float] = []
    payloads: list[float] = []
    warmup_switches = context.warmup if label == "switchWarm" else 0
    measured = 0
    for index, record in enumerate(records):
        chapter_id = str(record["chapterId"])
        revision_id = str(record["revisionId"])
        with operation(context) as fetch_box:
            fetch_response = client.get(
                f"/api/chapters/{chapter_id}/translation?chapter_id={chapter_id}"
            )
            fetch_box["bytes"] = float(len(fetch_response.content))
        if fetch_response.status_code != 200:
            raise FixtureError(f"{label}: GET translation HTTP {fetch_response.status_code} {fetch_response.text[:200]}")
        with operation(context) as draft_box:
            draft_response = client.get(
                f"/api/chapters/{chapter_id}/draft?baseRevisionId={revision_id}"
            )
            draft_box["bytes"] = float(len(draft_response.content))
        if draft_response.status_code != 200:
            raise FixtureError(f"{label}: GET draft HTTP {draft_response.status_code} {draft_response.text[:200]}")
        if index < warmup_switches:
            continue
        measured += 1
        combined.append(fetch_box["elapsedMs"] + draft_box["elapsedMs"])
        fetch_total.append(fetch_box["elapsedMs"])
        fetch_db.append(fetch_box["dbMs"])
        draft_total.append(draft_box["elapsedMs"])
        draft_db.append(draft_box["dbMs"])
        statements.append(fetch_box["statements"] + draft_box["statements"])
        payloads.append(fetch_box["bytes"] + draft_box["bytes"])
    if not combined:
        raise FixtureError(f"{label}: không có lượt đo nào")
    recorder.record(f"{label}.total", combined, "ms", note=note)
    recorder.record(
        f"{label}.fetch.total",
        fetch_total,
        "ms",
        note="Phần FETCH của lượt đổi chương: GET /api/chapters/{id}/translation (payload chương).",
    )
    recorder.record(
        f"{label}.fetch.db",
        fetch_db,
        "ms",
        note="Thời gian trong SQLite của riêng phần fetch (thread-local: TestClient chạy ASGI ở thread khác nên quy kết bằng process-global-delta, fixture chạy tuần tự).",
    )
    recorder.record(f"{label}.draft.total", draft_total, "ms", note="Phần khôi phục nháp của editor: GET /api/chapters/{id}/draft.")
    recorder.record(f"{label}.draft.db", draft_db, "ms")
    recorder.record(f"{label}.statements", statements, "statements", digits=STATEMENT_DIGITS)
    recorder.record(f"{label}.responseBytes", payloads, "bytes", digits=BYTE_DIGITS)
    recorder.add_query(label, statements)
    recorder.add_response_bytes(label, payloads)
    return {
        "switches": measured,
        "p95Ms": round(percentile(combined, 0.95), 3),
        "fetchP95Ms": round(percentile(fetch_total, 0.95), 3),
        "draftP95Ms": round(percentile(draft_total, 0.95), 3),
    }


# --------------------------------------------------------------------------- #
# Fixture EDITOR8TAB — C2K editor và 8 tab: cap 8 tab + không mất draft khi evict/reopen
# --------------------------------------------------------------------------- #


def _layout_tab(index: int, chapter_id: str, *, dirty: bool = False) -> dict[str, object]:
    return {
        "id": f"tab-{index}",
        "kind": "EDITOR",
        "projectId": None,
        "chapterId": chapter_id,
        "title": f"Chương {index + 1}",
        "dirty": dirty,
    }


def _layout_payload(project_id: str, tabs: list[dict[str, object]], active: str | None) -> dict[str, object]:
    normalized = [{**tab, "projectId": project_id} for tab in tabs]
    return {
        "version": 1,
        "projectId": project_id,
        "panes": {
            "primary": {"tabs": normalized, "activeTabId": active or (normalized[0]["id"] if normalized else None)},
            "secondary": {"tabs": [], "activeTabId": None},
        },
        "docked": False,
        "focus": "primary",
    }


def run_editor8tab(context: FixtureContext, recorder: Recorder) -> None:
    config = context.config
    tab_count = int(config["tabs"])
    segments_per_chapter = context.scaled(int(config["segmentsPerChapter"]), floor=2)
    chapters = context.scaled(int(config["chapters"]))
    tabs = max(tab_count, 2)
    engine = harness.bootstrap_database(context.data_root)
    try:
        with session_factory(engine)() as session:
            project_ids = seed_projects(
                session,
                seed=context.seed,
                fixture_name=context.spec.name,
                project_count=1,
                chapters_per_project=max(chapters, tabs + 1),
            )
            records = seed_translated_chapters(
                session,
                project_index=0,
                ordinals=list(range(1, tabs + 1)),
                segments_per_chapter=segments_per_chapter,
                seed=context.seed,
            )
        project_id = project_ids[0]
        app, client = _app_with_routers(context.data_root, create_workspace_router, create_drafts_router)
        with client:
            evidence = _editor8tab_probe(
                context,
                recorder,
                client,
                project_id=project_id,
                records=records,
                tab_count=tabs,
                chapters=chapters,
            )
    finally:
        engine.dispose()

    reducer = _node_driver_report(
        recorder,
        driver="layout_reducer.mjs",
        key="editor8tabReducer",
        not_run_note=(
            "Không có Node trong môi trường này nên không chạy được CHÍNH reducer workspace của frontend "
            "(frontend/src/features/workspace/workspaceLayout.ts). Harness không sao chép logic reducer sang "
            "Python để tránh claim sai; chỉ đo được hành vi phía server (route layout + draft)."
        ),
    )
    if reducer is not None:
        reducer_ok = bool(
            reducer.get("openAfterLimitStayedBounded")
            and reducer.get("dirtyOpenError") == "TAB_LIMIT_DIRTY"
            and reducer.get("dirtyOpenKeptLayout")
            and reducer.get("reopenCountForVictim") == 1
            and reducer.get("roundTripTabCount") == tabs
            and reducer.get("roundTripIdsMatch")
        )
        recorder.threshold_bool(
            case="C2K editor 8 tab — reducer 8 tab (module thật của frontend)",
            ok=reducer_ok,
            measured={
                "tabsAfterNinthOpen": reducer.get("tabsAfterNinthOpen"),
                "maxTabsPerPane": reducer.get("maxTabsPerPane"),
                "evictedTabId": reducer.get("evictedTabId"),
                "dirtyOpenError": reducer.get("dirtyOpenError"),
                "reopenCountForVictim": reducer.get("reopenCountForVictim"),
                "roundTripTabCount": reducer.get("roundTripTabCount"),
                "roundTripIdsMatch": reducer.get("roundTripIdsMatch"),
            },
            unit="tabs",
            label=f"≤{tabs} tab/pane, tab bẩn không bị đóng ngầm, reopen không nhân bản",
            target=tabs,
            note=(
                "Chạy CHÍNH reducer workspace (frontend/src/features/workspace/workspaceLayout.ts) bằng Node "
                "type-stripping: mở tab thứ 9 phải evict tab sạch, tab bẩn trả TAB_LIMIT_DIRTY, evict rồi "
                "reopen phải trả tab cũ đúng một lần. KHÔNG phải phép đo long task của browser."
            ),
        )
    recorder.threshold_not_run(
        case="C2K editor 8 tab — long task khi gõ",
        label="không long task >50 ms lặp trong thao tác gõ",
        reason=(
            "Cần browser thật (PerformanceObserver/long-task trace + IME) trên fixture 2.000 segment; harness "
            "offline này chỉ chạy tiến trình Python + Node headless, không có engine render/DOM nên không thể "
            "đo long task. Không suy ra PASS từ số đo reducer (xem extras.editor8tabReducer.reducerOpP95Ms)."
        ),
        unit="ms",
    )
    recorder.set_phase("db", "MEASURED", metric="editorLayoutSave.db")
    recorder.set_phase("queue", "NOT_RUN", note="EDITOR8TAB chỉ đọc/ghi layout + nháp, không đi qua worker queue.")
    _set_provider_phases(recorder)
    recorder.threshold_bool(
        case="C2K editor 8 tab — không mất draft khi evict/reopen",
        ok=bool(evidence["draftPreserved"]),
        measured={
            "tabsSaved": evidence["tabsSaved"],
            "tabsAfterCap": evidence["tabsAfterCap"],
            "tabsAfterEvict": evidence["tabsAfterEvict"],
            "tabsAfterReopen": evidence["tabsAfterReopen"],
            "draftChapters": evidence["draftChapters"],
            "draftsRestoredIdentical": evidence["draftsRestoredIdentical"],
            "revisionStableOnReopen": evidence["revisionStableOnReopen"],
        },
        unit="draft",
        label="nội dung nháp còn nguyên sau evict + reopen",
        target=evidence["draftChapters"],
        note=(
            "Đo trên route thật: PUT /api/projects/{id}/workspace-layout (8 tab, rồi 9 tab để xem server kẹp, "
            "rồi evict + reopen) và PUT/GET /api/chapters/{id}/draft. Nháp khôi phục sau reopen phải trùng "
            "từng byte với nháp đã lưu và revision không đổi."
        ),
    )
    recorder.threshold_bool(
        case="C2K editor 8 tab — server kẹp ≤8 tab/pane",
        ok=bool(evidence["serverCapEnforced"]),
        measured={
            "tabsRequested": evidence["tabsRequestedInCapProbe"],
            "tabsKept": evidence["tabsAfterCap"],
            "maxTabsPerPane": evidence["maxTabsPerPaneServer"],
        },
        unit="tabs",
        label=f"server chỉ giữ {tabs} tab/pane",
        target=tabs,
        note="Route workspace-layout sanitize payload (modules/workspace/layout.py: MAX_TABS_PER_PANE).",
    )


def _editor8tab_probe(
    context: FixtureContext,
    recorder: Recorder,
    client: TestClient,
    *,
    project_id: str,
    records: list[dict[str, object]],
    tab_count: int,
    chapters: int,
) -> dict[str, Any]:
    layout_url = f"/api/projects/{project_id}/workspace-layout"
    tabs = [_layout_tab(index, str(record["chapterId"])) for index, record in enumerate(records)]
    saved = client.put(layout_url, json={"layout": _layout_payload(project_id, tabs, "tab-0"), "expected_revision": None})
    if saved.status_code != 200:
        raise FixtureError(f"PUT layout: HTTP {saved.status_code} {saved.text[:200]}")
    revision = int(saved.json()["revision"])
    tabs_saved = len(saved.json()["layout"]["panes"]["primary"]["tabs"])

    _measure_endpoint(
        context,
        recorder,
        client,
        url=layout_url,
        label="editorLayoutRead",
        rows_key=None,
        note="GET /api/projects/{id}/workspace-layout — khôi phục layout khi mở lại workspace.",
    )

    overflow = list(tabs) + [_layout_tab(tab_count, str(_chapter_id(0, tab_count + 1)))]
    capped = client.put(
        layout_url,
        json={"layout": _layout_payload(project_id, overflow, f"tab-{tab_count}"), "expected_revision": revision},
    )
    if capped.status_code != 200:
        raise FixtureError(f"PUT layout (9 tab): HTTP {capped.status_code} {capped.text[:200]}")
    revision = int(capped.json()["revision"])
    tabs_after_cap = len(capped.json()["layout"]["panes"]["primary"]["tabs"])

    revisions: dict[str, int] = {}
    saved_content: dict[str, dict[str, str]] = {}
    for record in records:
        chapter_id = str(record["chapterId"])
        content = {
            str(segment_id): f"Nhap cua chuong {record['ordinal']} doan {index}"
            for index, segment_id in enumerate(record["sourceSegmentIds"])
        }
        response = _timed_put(
            context,
            recorder,
            client,
            url=f"/api/chapters/{chapter_id}/draft",
            json_body={"base_revision_id": record["revisionId"], "content": content, "expected_revision": None},
            label="editorDraftSave",
            note="PUT /api/chapters/{id}/draft — lưu nháp phân đoạn của editor (compare-and-swap revision).",
        )
        if response.status_code != 200:
            raise FixtureError(f"PUT draft: HTTP {response.status_code} {response.text[:200]}")
        revisions[chapter_id] = int(response.json()["draft"]["revision"])
        saved_content[chapter_id] = content

    # Evict tab 1 (đóng tab sạch) rồi reopen: nháp của chương đó phải còn nguyên.
    victim = tabs[1]
    evicted_layout = _layout_payload(project_id, [tab for tab in tabs if tab["id"] != victim["id"]], "tab-0")
    evicted = client.put(layout_url, json={"layout": evicted_layout, "expected_revision": revision})
    if evicted.status_code != 200:
        raise FixtureError(f"PUT layout (evict): HTTP {evicted.status_code} {evicted.text[:200]}")
    revision = int(evicted.json()["revision"])
    tabs_after_evict = len(evicted.json()["layout"]["panes"]["primary"]["tabs"])

    reopened_layout = _layout_payload(project_id, tabs, victim["id"])
    reopened = client.put(layout_url, json={"layout": reopened_layout, "expected_revision": revision})
    if reopened.status_code != 200:
        raise FixtureError(f"PUT layout (reopen): HTTP {reopened.status_code} {reopened.text[:200]}")
    revision = int(reopened.json()["revision"])
    reopened_tabs = reopened.json()["layout"]["panes"]["primary"]["tabs"]
    tabs_after_reopen = len(reopened_tabs)

    restored: dict[str, Any] = {}
    identical = 0
    revision_stable = 0
    for record in records:
        chapter_id = str(record["chapterId"])
        response = _timed_get(
            context,
            recorder,
            client,
            url=f"/api/chapters/{chapter_id}/draft?baseRevisionId={record['revisionId']}",
            label="editorDraftRestore",
            note="GET /api/chapters/{id}/draft — nháp khôi phục khi tab được mở lại sau evict.",
        )
        if response.status_code != 200:
            raise FixtureError(f"GET draft: HTTP {response.status_code} {response.text[:200]}")
        draft = response.json().get("draft") or {}
        content = {str(key): str(value) for key, value in (draft.get("content") or {}).items()}
        restored[chapter_id] = {"revision": draft.get("revision"), "segments": len(content)}
        if content == saved_content[chapter_id]:
            identical += 1
        if int(draft.get("revision") or -1) == revisions[chapter_id]:
            revision_stable += 1

    return {
        "tabsSaved": tabs_saved,
        "tabsAfterCap": tabs_after_cap,
        "tabsAfterEvict": tabs_after_evict,
        "tabsAfterReopen": tabs_after_reopen,
        "tabsRequestedInCapProbe": len(overflow),
        "maxTabsPerPaneServer": MAX_MOUNTED_TABS_PER_PANE,
        "serverCapEnforced": tabs_after_cap == MAX_MOUNTED_TABS_PER_PANE and tabs_after_evict == MAX_MOUNTED_TABS_PER_PANE - 1,
        "draftChapters": len(records),
        "draftsRestoredIdentical": identical,
        "revisionStableOnReopen": revision_stable,
        "draftPreserved": identical == len(records) and revision_stable == len(records) and tabs_after_reopen == MAX_MOUNTED_TABS_PER_PANE,
        "restored": restored,
        "seededChapters": chapters,
    }


def _timed_get(
    context: FixtureContext,
    recorder: Recorder,
    client: TestClient,
    *,
    url: str,
    label: str,
    note: str,
) -> Any:
    return _timed_request(context, recorder, client, url=url, label=label, note=note, method="GET")


def _timed_put(
    context: FixtureContext,
    recorder: Recorder,
    client: TestClient,
    *,
    url: str,
    json_body: dict[str, object],
    label: str,
    note: str,
) -> Any:
    return _timed_request(
        context, recorder, client, url=url, label=label, note=note, method="PUT", json_body=json_body
    )


def _timed_request(
    context: FixtureContext,
    recorder: Recorder,
    client: TestClient,
    *,
    url: str,
    label: str,
    note: str,
    method: str,
    json_body: dict[str, object] | None = None,
) -> Any:
    request_label = f"{label}.total"
    db_label = f"{label}.db"
    statements_label = f"{label}.statements"
    with operation(context) as box:
        response = client.put(url, json=json_body) if method == "PUT" else client.get(url)
        box["bytes"] = float(len(response.content))
    recorder.record(request_label, [box["elapsedMs"]], "ms", note=note)
    recorder.record(db_label, [box["dbMs"]], "ms")
    recorder.record(statements_label, [box["statements"]], "statements", digits=STATEMENT_DIGITS)
    recorder.add_query(label, [box["statements"]])
    recorder.add_response_bytes(label, [box["bytes"]])
    return response




# --------------------------------------------------------------------------- #
# Fixture CANCELCKPT — cancel sau checkpoint: p95 ≤5 s, thời gian provider báo riêng
# --------------------------------------------------------------------------- #


class _InstrumentedTts:
    """FakeTts thật (offline) + đồng hồ đo provider và điểm kích hoạt cancel."""

    def __init__(self, *, latency_ms: int = 0, cancel_on_call: int | None = None, on_cancel=None) -> None:
        self._adapter = FakeTts()
        self._latency_ms = int(latency_ms)
        self.cancel_on_call = cancel_on_call
        self.on_cancel = on_cancel
        self.calls_started = 0
        self.calls_finished = 0
        self.started_at: dict[int, float] = {}
        self.finished_at: dict[int, float] = {}
        self.call_durations_ms: list[float] = []

    def capabilities(self) -> dict[str, object]:
        return self._adapter.capabilities()

    async def list_voices(self, locale: str) -> list[dict[str, object]]:
        return await self._adapter.list_voices(locale)

    async def synthesize(self, request: Any, output_path: Path) -> Any:
        self.calls_started += 1
        call_index = self.calls_started
        self.started_at[call_index] = time.perf_counter()
        if self.on_cancel is not None and self.cancel_on_call == call_index:
            # "Cancel sau checkpoint": người dùng bấm cancel khi segment này đang được tổng hợp.
            self.on_cancel()
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)
        result = await self._adapter.synthesize(request, output_path)
        self.finished_at[call_index] = time.perf_counter()
        self.calls_finished = call_index
        self.call_durations_ms.append((self.finished_at[call_index] - self.started_at[call_index]) * 1000.0)
        return result


def _worker_for_render(engine: Engine, artifact_root: Path, tts: Any) -> Worker:
    handler = build_synthesize_handler(
        tts=tts,
        audio_processor=FakeMp3AudioProcessor(),
        allow_fake_tts=True,
    )
    artifact_root.mkdir(parents=True, exist_ok=True)
    return Worker(
        JobRunner(engine),
        handlers={JobKind.SYNTHESIZE: handler},
        artifact_root=artifact_root,
    )


def _enqueue_synthesize(runner: JobRunner, *, project_id: str, chapter_id: str, plan_id: str, run_id: str, key: str) -> Any:
    return runner.enqueue(
        JobKind.SYNTHESIZE,
        project_id,
        chapter_id,
        key,
        plan={
            "projectId": project_id,
            "profileId": "local-tts",
            "cloudConsentId": "",
            "budgetAuthorizationId": "",
            "translationRunId": run_id,
            "voicePlanId": plan_id,
        },
    )


def _ready_segment_artifacts(engine: Engine, chapter_id: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT COUNT(*) FROM artifacts WHERE chapter_id = :chapter_id "
                    "AND kind = :kind AND status = 'READY'"
                ),
                {"chapter_id": chapter_id, "kind": ArtifactKind.TTS_SEGMENT.value},
            ).scalar_one()
        )


def _staging_leftovers(artifact_root: Path) -> list[str]:
    if not artifact_root.is_dir():
        return []
    return sorted(
        path.name
        for path in artifact_root.rglob("*")
        if path.is_file() and (path.name.endswith(".partial") or path.name.startswith(".tmp-"))
    )


def run_cancelckpt(context: FixtureContext, recorder: Recorder) -> None:
    config = context.config
    segments = max(3, context.scaled(int(config["segmentsPerChapter"]), floor=3))
    cancel_after = max(1, min(int(config["cancelAfterSegments"]), segments - 1))
    chapters_needed = max(2, context.warmup + context.iterations)
    artifact_root = context.data_root / "artifacts"
    engine = harness.bootstrap_database(context.data_root)
    try:
        with session_factory(engine)() as session:
            project_ids = seed_projects(
                session,
                seed=context.seed,
                fixture_name=context.spec.name,
                project_count=1,
                chapters_per_project=chapters_needed,
            )
            records = seed_translated_chapters(
                session,
                project_index=0,
                ordinals=list(range(1, chapters_needed + 1)),
                segments_per_chapter=segments,
                seed=context.seed,
            )
            preset = seed_voice_preset(session)
        project_id = project_ids[0]
        with session_factory(engine)() as session:
            for record in records:
                plan = SpeechWorkflow(session).configure_single(str(record["chapterId"]), preset.id)
                record["planId"] = plan.id
            session.commit()

        latencies: list[float] = []
        provider_tails: list[float] = []
        state_confirmed: list[bool] = []
        ready_after_cancel: list[int] = []
        resume_calls: list[float] = []
        resume_seconds: list[float] = []
        iterations = 0
        for index, record in enumerate(records):
            chapter_id = str(record["chapterId"])
            plan_id = str(record["planId"])
            run_id = str(record["runId"])
            outcome = _cancel_iteration(
                context,
                engine,
                artifact_root=artifact_root,
                project_id=project_id,
                chapter_id=chapter_id,
                plan_id=plan_id,
                run_id=run_id,
                cancel_after=cancel_after,
                segments=segments,
                key=f"cancel-{index}",
            )
            if index < context.warmup:
                continue
            iterations += 1
            latencies.append(outcome["cancelLatencyMs"])
            provider_tails.append(outcome["providerTailMs"])
            state_confirmed.append(bool(outcome["cancelRequestedObserved"]))
            ready_after_cancel.append(int(outcome["readyAfterCancel"]))
            resume_calls.append(float(outcome["resumeProviderCalls"]))
            resume_seconds.append(float(outcome["resumeSeconds"]) * 1000.0)
    finally:
        engine.dispose()

    if not latencies:
        raise FixtureError("CANCELCKPT: không có lượt đo nào")
    recorder.record(
        "cancel.latency",
        latencies,
        "ms",
        note=(
            "Từ lúc CANCEL_REQUESTED (request_cancel trả về) tới khi worker đã ack và job ở trạng thái "
            "CANCELED (đo sau khi run_once kết thúc: cận trên, gồm cả ack + dọn heartbeat)."
        ),
    )
    recorder.record(
        "cancel.providerTail",
        provider_tails,
        "ms",
        note=(
            "Thời gian provider (FakeTts) kết thúc cho segment đang bay khi cancel tới — BÁO RIÊNG, "
            "đây là phần nằm trong ngân sách cancel ở trên nên phải nhìn cùng nhau."
        ),
    )
    recorder.record(
        "cancel.resume.total",
        resume_seconds,
        "ms",
        note="Lượt resume sau cancel: chạy lại job SYNTHESIZE tới khi SUCCEEDED (phải trúng cache phần đã xong).",
    )
    recorder.extras["cancel"] = {
        "segmentsPerChapter": segments,
        "cancelAfterSegments": cancel_after,
        "iterations": iterations,
        "fakeProviderLatencyMs": context.fake_latency_ms,
        "cancelRequestedObserved": sum(1 for value in state_confirmed if value),
        "readyAfterCancel": ready_after_cancel,
        "readyAfterCancelExpected": cancel_after,
        "providerTailMaxMs": round(max(provider_tails), 3),
        "resumeProviderCalls": resume_calls,
        "resumeProviderCallsExpected": segments - cancel_after,
        "stagingLeftovers": _staging_leftovers(artifact_root),
    }
    recorder.set_measurement(
        "operation",
        (
            f"Worker thật + handler SYNTHESIZE thật + FakeTts (offline) render {segments} segment; cancel được "
            f"kích hoạt khi segment thứ {cancel_after} đang tổng hợp, rồi job được resume để chạy hết."
        ),
    )
    recorder.note(
        "Đây là cancel với provider GIẢ (FakeTts): chứng minh plumbing cancel/ack/checkpoint, KHÔNG phải "
        "cancel một provider cloud thật và không suy ra hành vi mạng của nhà cung cấp."
    )
    recorder.set_phase("provider", "MEASURED", metric="cancel.providerTail", note="FakeTts offline: thời gian tổng hợp báo riêng, không gộp vào ngân sách DB/API.")
    recorder.set_phase("db", "MEASURED", metric="cancel.latency")
    recorder.set_phase("queue", "MEASURED", metric="cancel.latency", note="Job đi qua JobRunner/Worker thật: claim → lease → ack cancel.")
    recorder.set_phase("ffmpeg", "NOT_RUN", note="Không chạy FFmpeg: handler SYNTHESIZE chỉ render segment audio bằng fake adapter.")
    recorder.threshold_le(
        case="Cancel sau checkpoint",
        target=5_000,
        measured=recorder.p("cancel.latency", 0.95),
        unit="ms",
        label="p95 ≤ 5 s",
        note=(
            f"p95 = {recorder.p('cancel.latency', 0.95):.3f} ms trên {iterations} lượt; thời gian provider "
            f"kết thúc (FakeTts) lớn nhất {max(provider_tails):.3f} ms được báo riêng ở "
            "cancel.providerTail (--fake-latency-ms đặt được để mô phỏng provider chậm)."
        ),
    )
    recorder.threshold_bool(
        case="Cancel sau checkpoint — checkpoint giữ nguyên",
        ok=all(value == cancel_after for value in ready_after_cancel) and not _staging_leftovers(artifact_root),
        measured={
            "readyAfterCancel": sorted(set(ready_after_cancel)),
            "expected": cancel_after,
            "stagingLeftovers": len(_staging_leftovers(artifact_root)),
            "resumeProviderCalls": sorted(set(resume_calls)),
            "resumeExpected": segments - cancel_after,
        },
        unit="segment",
        label=f"{cancel_after} segment đã READY được giữ, không còn file dở, resume chỉ gọi lại phần thiếu",
        target=cancel_after,
        note="Cancel dừng ở ranh giới segment: phần đã checkpoint vẫn READY và lượt resume trúng cache phần đó.",
    )


def _cancel_iteration(
    context: FixtureContext,
    engine: Engine,
    *,
    artifact_root: Path,
    project_id: str,
    chapter_id: str,
    plan_id: str,
    run_id: str,
    cancel_after: int,
    segments: int,
    key: str,
) -> dict[str, Any]:
    """Một lượt đo cancel: enqueue → worker chạy → cancel giữa segment → ack → resume."""

    stamp: dict[str, float] = {}
    runner_holder: dict[str, Any] = {}
    job_holder: dict[str, str] = {}

    def request_cancel() -> None:
        runner = runner_holder["runner"]
        started = time.perf_counter()
        runner.request_cancel(job_holder["job_id"], datetime.now(UTC))
        stamp["request_at"] = started
        stamp["request_out_at"] = time.perf_counter()

    tts = _InstrumentedTts(
        latency_ms=context.fake_latency_ms,
        cancel_on_call=cancel_after,
        on_cancel=request_cancel,
    )
    worker = _worker_for_render(engine, artifact_root, tts)
    runner_holder["runner"] = worker.runner
    job = _enqueue_synthesize(
        worker.runner,
        project_id=project_id,
        chapter_id=chapter_id,
        plan_id=plan_id,
        run_id=run_id,
        key=key,
    )
    job_holder["job_id"] = job.id

    cancel_requested_observed = False
    asyncio.run(worker.run_once())
    finished_at = time.perf_counter()
    view = worker.runner.get(job.id)
    cancel_requested_observed = "request_out_at" in stamp and view.status is JobStatus.CANCELED
    if view.status is not JobStatus.CANCELED:
        raise FixtureError(f"CANCELCKPT: job {job.id} kết thúc ở trạng thái {view.status.value}, không phải CANCELED")
    if "request_out_at" not in stamp:
        raise FixtureError("CANCELCKPT: TTS chưa bao giờ gọi request_cancel (số segment ít hơn cancel_after?)")

    cancel_latency_ms = (finished_at - stamp["request_out_at"]) * 1000.0
    provider_finish = tts.finished_at.get(cancel_after, stamp["request_out_at"])
    provider_tail_ms = max(0.0, (provider_finish - stamp["request_out_at"]) * 1000.0)
    ready = _ready_segment_artifacts(engine, chapter_id)

    resumed_tts = _InstrumentedTts(latency_ms=context.fake_latency_ms)
    resume_worker = _worker_for_render(engine, artifact_root, resumed_tts)
    resume_job = _enqueue_synthesize(
        resume_worker.runner,
        project_id=project_id,
        chapter_id=chapter_id,
        plan_id=plan_id,
        run_id=run_id,
        key=f"{key}-resume",
    )
    resume_started = time.perf_counter()
    asyncio.run(resume_worker.run_once())
    resume_seconds = time.perf_counter() - resume_started
    resume_view = resume_worker.runner.get(resume_job.id)
    if resume_view.status is not JobStatus.SUCCEEDED:
        raise FixtureError(f"CANCELCKPT: lượt resume kết thúc ở {resume_view.status.value}, không phải SUCCEEDED")
    ready_after_resume = _ready_segment_artifacts(engine, chapter_id)
    if ready_after_resume != segments:
        raise FixtureError(f"CANCELCKPT: resume chỉ có {ready_after_resume}/{segments} segment READY")

    return {
        "cancelLatencyMs": cancel_latency_ms,
        "providerTailMs": provider_tail_ms,
        "cancelRequestedObserved": cancel_requested_observed,
        "readyAfterCancel": ready,
        "readyAfterResume": ready_after_resume,
        "resumeProviderCalls": resumed_tts.calls_finished,
        "resumeSeconds": resume_seconds,
    }




# --------------------------------------------------------------------------- #
# Fixture SSE30MIN — phiên SSE rút ngắn: store ≤1.000 metadata, ≤4 frame/s/job
# --------------------------------------------------------------------------- #


def run_sse30min(context: FixtureContext, recorder: Recorder) -> None:
    config = context.config
    chapters = max(2, context.scaled(int(config["chapters"])))
    segments = max(2, context.scaled(int(config["segmentsPerChapter"]), floor=2))
    jobs = max(2, context.scaled(int(config["jobs"])))
    configured_seconds = float(context.session_seconds or config["sessionSeconds"])
    session_seconds = max(1.0, configured_seconds)
    artifact_root = context.data_root / "artifacts"
    engine = harness.bootstrap_database(context.data_root)
    frames_path = context.data_root / "sse-frames.json"
    try:
        with session_factory(engine)() as session:
            project_ids = seed_projects(
                session,
                seed=context.seed,
                fixture_name=context.spec.name,
                project_count=1,
                chapters_per_project=chapters,
            )
            records = seed_translated_chapters(
                session,
                project_index=0,
                ordinals=list(range(1, chapters + 1)),
                segments_per_chapter=segments,
                seed=context.seed,
            )
            preset = seed_voice_preset(session)
        project_id = project_ids[0]
        with session_factory(engine)() as session:
            for record in records:
                plan = SpeechWorkflow(session).configure_single(str(record["chapterId"]), preset.id)
                record["planId"] = plan.id
            session.commit()

        worker = _worker_for_render(engine, artifact_root, _InstrumentedTts())

        recorder.mark_rss("sseBeforeSession")
        stop = threading.Event()
        worker_errors: list[str] = []
        enqueued = [0]

        def render_loop() -> None:
            while not stop.is_set():
                try:
                    worked = asyncio.run(worker.run_once())
                except Exception as exc:  # noqa: BLE001 - phiên đo phải báo lỗi chứ không treo
                    worker_errors.append(f"{type(exc).__name__}: {exc}")
                    return
                if not worked:
                    time.sleep(0.02)

        def feed_loop() -> None:
            # Job mới được đẩy vào hàng đợi rải đều suốt phiên: feed SSE phát một frame cho mỗi
            # entity job mới, nên phiên dài cần dòng job chảy liên tục (không dồn hết một giây).
            interval = max(0.05, session_seconds / max(1, jobs))
            for index in range(jobs):
                if stop.is_set():
                    return
                record = records[index % len(records)]
                try:
                    _enqueue_synthesize(
                        worker.runner,
                        project_id=project_id,
                        chapter_id=str(record["chapterId"]),
                        plan_id=str(record["planId"]),
                        run_id=str(record["runId"]),
                        key=f"sse-session-{index}-{int(time.time() * 1000)}",
                    )
                    enqueued[0] += 1
                except Exception as exc:  # noqa: BLE001
                    worker_errors.append(f"enqueue: {type(exc).__name__}: {exc}")
                    return
                time.sleep(interval)

        thread = threading.Thread(target=render_loop, name="sse-render", daemon=True)
        feeder = threading.Thread(target=feed_loop, name="sse-feed", daemon=True)
        thread.start()
        feeder.start()
        try:
            session = _sse_session(
                context,
                recorder,
                engine=engine,
                data_root=context.data_root,
                duration_seconds=session_seconds,
                snapshot_interval_ms=int(config["snapshotIntervalMs"]),
                frames_path=frames_path,
            )
        finally:
            stop.set()
            thread.join(timeout=60)
            feeder.join(timeout=60)
        recorder.mark_rss("sseAfterSession")
    finally:
        engine.dispose()

    frames = session["frames"]
    per_job = session["maxFramesPerJobPerSecond"]
    recorder.extras["sseSession"] = {
        "configuredSessionSeconds": round(configured_seconds, 3),
        "sessionSeconds": round(session["durationSeconds"], 3),
        "shortened": session["durationSeconds"] < 1800.0,
        "thresholdSecondsFromPlan": 1800,
        "snapshotCycles": session["snapshotCycles"],
        "streamCycles": session["streamCycles"],
        "frames": len(frames),
        "framesPerSecond": round(session["framesPerSecond"], 4),
        "distinctJobs": session["distinctJobs"],
        "maxFramesPerJobPerSecond": per_job,
        "maxFrameBytes": session["maxFrameBytes"],
        "meanFrameBytes": round(session["meanFrameBytes"], 2),
        "totalFrameBytes": session["totalFrameBytes"],
        "snapshotEventsMax": session["snapshotEventsMax"],
        "workerErrors": worker_errors,
        "jobsEnqueued": enqueued[0],
        "framesPath": frames_path.name,
        "replayRepeatForThirtyMinutes": int(_thirty_minute_repeat(session["durationSeconds"])),
    }
    recorder.note(
        "Phiên SSE30MIN bị RÚT NGẮN: plan yêu cầu phiên 30 phút và so heap phút 30 với phút 5; harness chỉ "
        f"chạy {session['durationSeconds']:.1f} s nên dòng ngưỡng 30 phút được ghi NOT_RUN kèm số đo rút ngắn."
    )
    recorder.set_measurement(
        "operation",
        (
            f"Phiên SSE thật {session['durationSeconds']:.1f} s: worker thật render {jobs} job trên {chapters} chương "
            "trong lúc client lặp snapshot (GET /api/jobs/snapshot) + replay (GET /api/jobs/events) như store frontend."
        ),
    )
    recorder.set_phase("db", "MEASURED", metric="sseSession.framesPerSecond")
    recorder.set_phase("queue", "MEASURED", metric="sseSession.frames", note="Job thật chạy qua JobRunner/Worker trong phiên.")
    recorder.set_phase(
        "provider",
        "MEASURED",
        metric="sseSession.frames",
        note="FakeTts offline trong phiên (không gọi provider thật); job/provider báo riêng khỏi ngân sách API/DB.",
    )
    recorder.set_phase("ffmpeg", "NOT_RUN", note="Không chạy FFmpeg trong phiên SSE.")

    frames_path.write_text(
        json.dumps(
            {"frames": frames, "repeat": int(_thirty_minute_repeat(session["durationSeconds"]))},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = _node_driver_report(
        recorder,
        driver="job_store_session.mjs",
        key="sseStoreSession",
        arguments=[str(frames_path)],
        expose_gc=True,
        not_run_note=(
            "Không có Node trong môi trường này nên không replay được trace frame thật vào CHÍNH store "
            "frontend/src/features/jobs/jobStore.ts; harness không sao chép store sang Python để tránh claim sai."
        ),
    )
    if store is not None:
        stored = int(store.get("maxStoredDuringReplay") or 0)
        recorder.threshold_le(
            case="SSE 30 phút — store metadata",
            target=1_000,
            measured=float(stored),
            unit="metadata",
            label="store ≤ 1.000 metadata",
            note=(
                "Đo bằng CHÍNH store frontend (U07) chạy trên Node với trace frame thật của phiên này, lặp "
                f"{store.get('repeat')}× để mô phỏng khối lượng frame của phiên 30 phút (phiên thật bị rút "
                "ngắn): tổng "
                f"{store.get('framesReplayed')} frame replay → lớn nhất {stored} metadata trong buffer "
                f"(MAX_EVENTS={store.get('maxEvents')}), truncated={store.get('truncated')}."
            ),
        )
    recorder.threshold_le(
        case="SSE 30 phút — delta frame mỗi job",
        target=4,
        measured=float(per_job),
        unit="frame/s/job",
        label="delta ≤ 4 frame/s/job",
        note=(
            "Số frame lớn nhất mà MỘT job nhận trong một cửa sổ 1 giây trên feed thật "
            f"({len(frames)} frame, {session['distinctJobs']} job, {session['durationSeconds']:.1f} s). "
            "Lưu ý: feed hiện phát một frame cho mỗi entity mới trong event_log (payload mang trạng thái hiện "
            "tại), nên tỉ lệ này bị chi phối bởi số entity mới mỗi giây chứ chưa phải coalescing tiến độ."
        ),
    )
    recorder.threshold_not_run(
        case="SSE 30 phút — phiên đủ 30 phút và heap phút 30 so với phút 5",
        label="heap sau GC phút 30 tăng ≤20 MiB so với phút 5",
        reason=(
            f"RÚT NGẮN, KHÔNG ĐỦ 30 PHÚT: phiên đo chỉ {session['durationSeconds']:.1f} s "
            f"(plan §7 yêu cầu 1.800 s). Số đo rút ngắn: RSS tiến trình {recorder.rss_report()['delta']} MiB, "
            f"{len(frames)} frame/{session['durationSeconds']:.1f} s, store tối đa "
            f"{(store or {}).get('maxStoredDuringReplay')} metadata, heap Node sau GC thay đổi "
            f"{(store or {}).get('heapDeltaMb')} MiB (heap Node KHÔNG phải heap browser). "
            "Không suy ra PASS cho mốc 30 phút từ phiên ngắn."
        ),
        unit="MiB",
    )


def _sse_session(
    context: FixtureContext,
    recorder: Recorder,
    *,
    engine: Engine,
    data_root: Path,
    duration_seconds: float,
    snapshot_interval_ms: int,
    frames_path: Path,
) -> dict[str, Any]:
    """Phiên client thật: snapshot rồi replay qua route SSE, lặp lại như store frontend."""

    app, client = _app_with_routers(data_root, create_jobs_router)
    frames: list[dict[str, object]] = []
    snapshot_events_max = 0
    snapshot_cycles = 0
    stream_cycles = 0
    cursor: str | None = None
    started = time.perf_counter()
    deadline = started + duration_seconds
    snapshot_total: list[float] = []
    snapshot_db: list[float] = []
    snapshot_statements: list[float] = []
    snapshot_bytes: list[float] = []
    attributions: list[str] = []
    stream_total: list[float] = []
    stream_db: list[float] = []
    stream_statements: list[float] = []
    with client:
        while time.perf_counter() < deadline:
            with operation(context) as box:
                snapshot_response = client.get("/api/jobs/snapshot")
                box["bytes"] = float(len(snapshot_response.content))
            if snapshot_response.status_code != 200:
                raise FixtureError(f"snapshot: HTTP {snapshot_response.status_code}")
            events = snapshot_response.json().get("events") or []
            snapshot_events_max = max(snapshot_events_max, len(events))
            snapshot_cycles += 1
            snapshot_total.append(box["elapsedMs"])
            snapshot_db.append(box["dbMs"])
            snapshot_statements.append(box["statements"])
            snapshot_bytes.append(box["bytes"])
            attributions.append(str(box["dbAttribution"]))

            url = "/api/jobs/events" if cursor is None else f"/api/jobs/events?after={quote(str(cursor))}"
            with operation(context) as stream_box:
                received = 0
                with client.stream("GET", url) as response:
                    for line in response.iter_lines():
                        if time.perf_counter() >= deadline:
                            break
                        if not line.startswith("data:"):
                            continue
                        payload = json.loads(line[5:].strip())
                        received += 1
                        frames.append(
                            {
                                "sequenceId": payload.get("sequenceId"),
                                "jobId": payload.get("jobId"),
                                "status": payload.get("status"),
                                "current": payload.get("current"),
                                "total": payload.get("total"),
                                "tMs": round((time.perf_counter() - started) * 1000.0, 3),
                            }
                        )
                        cursor = payload.get("sequenceId", cursor)
                stream_box["bytes"] = float(received)
            stream_cycles += 1
            stream_total.append(stream_box["elapsedMs"])
            stream_db.append(stream_box["dbMs"])
            stream_statements.append(stream_box["statements"])
            time.sleep(max(0.0, snapshot_interval_ms / 1000.0))
    duration = time.perf_counter() - started
    _record_api_metrics(
        recorder,
        label="sseSnapshot",
        total=snapshot_total,
        db_ms=snapshot_db,
        statements=snapshot_statements,
        payloads=snapshot_bytes,
        attributions=attributions,
        note="Mỗi chu kỳ store refetch snapshot (GET /api/jobs/snapshot) trước khi replay tiếp — đúng hành vi reconnect của store.",
    )
    recorder.record(
        "sseReplayCycle.total",
        stream_total,
        "ms",
        note="Một chu kỳ replay qua route SSE thật (GET /api/jobs/events?after=cursor).",
    )
    recorder.record("sseReplayCycle.db", stream_db, "ms")
    recorder.record("sseReplayCycle.statements", stream_statements, "statements", digits=STATEMENT_DIGITS)
    recorder.add_query("sseReplayCycle", stream_statements)

    frames_path.write_text(json.dumps({"frames": frames}, ensure_ascii=False), encoding="utf-8")
    per_job = _max_frames_per_job_per_second(frames)
    payload_sizes = [len(json.dumps(frame, separators=(",", ":"))) for frame in frames]
    return {
        "frames": frames,
        "durationSeconds": duration,
        "snapshotCycles": snapshot_cycles,
        "streamCycles": stream_cycles,
        "snapshotEventsMax": snapshot_events_max,
        "distinctJobs": len({frame["jobId"] for frame in frames}),
        "maxFramesPerJobPerSecond": per_job,
        "framesPerSecond": (len(frames) / duration) if duration > 0 else 0.0,
        "maxFrameBytes": max(payload_sizes) if payload_sizes else 0,
        "meanFrameBytes": (sum(payload_sizes) / len(payload_sizes)) if payload_sizes else 0.0,
        "totalFrameBytes": sum(payload_sizes),
    }


def _thirty_minute_repeat(session_seconds: float) -> float:
    """Hệ số lặp trace để mô phỏng khối lượng frame của phiên 30 phút (plan §7)."""

    return max(1.0, 1800.0 / max(1.0, float(session_seconds)))


def _max_frames_per_job_per_second(frames: list[dict[str, object]]) -> int:
    per_job: dict[str, list[float]] = {}
    for frame in frames:
        per_job.setdefault(str(frame["jobId"]), []).append(float(frame["tMs"]))
    worst = 0
    for times in per_job.values():
        ordered = sorted(times)
        start = 0
        for index, value in enumerate(ordered):
            while value - ordered[start] > 1000.0:
                start += 1
            worst = max(worst, index - start + 1)
    return worst


# --------------------------------------------------------------------------- #
# Fixture BACKUP — backup thật trên DB tạm lớn: integrity/checksum/reference, WAL
# --------------------------------------------------------------------------- #


def run_backup(context: FixtureContext, recorder: Recorder) -> None:
    config = context.config
    chapters = context.scaled(int(config["chapters"]))
    artifact_count = context.scaled(int(config["artifacts"]))
    payload_bytes = int(config["payloadBytes"])
    retention = int(config["retention"])
    writer_ops = max(1, context.scaled(int(config["writerOps"])))
    artifact_root = context.data_root / "artifacts"
    backup_root = context.data_root / "backups"
    db_path = harness.database_path(context.data_root)
    engine = harness.bootstrap_database(context.data_root)
    try:
        with session_factory(engine)() as session:
            project_ids = seed_projects(
                session,
                seed=context.seed,
                fixture_name=context.spec.name,
                project_count=1,
                chapters_per_project=chapters,
            )
        project_id = project_ids[0]
        seed_artifact_files(
            engine,
            artifact_root=artifact_root,
            project_index=0,
            count=artifact_count,
            payload_bytes=payload_bytes,
            seed=context.seed,
        )
        # Hàng chỉ nằm trong WAL: commit qua sqlite3 (WAL) rồi KHÔNG checkpoint.
        wal_marker_count = _commit_wal_only_rows(db_path, project_id=project_id, count=3)
        wal_before = harness.wal_sizes(context.data_root)
        main_copy_rows = _rows_in_main_file_only(db_path, context.data_root)
        service = BackupService(
            db_path=db_path,
            backup_root=backup_root,
            artifact_root=artifact_root,
            retention_count=retention,
            api_lock_path=context.data_root / "studio-api.lock",
            worker_lock_path=context.data_root / "studio-worker.lock",
        )
        creates: list[float] = []
        verifies: list[float] = []
        integrity_all_ok = True
        checksum_all_ok = True
        reference_all_ok = True
        wal_rows_in_snapshots: list[int] = []
        wal_files_in_backups: list[str] = []
        verification_errors: list[str] = []
        for index in range(context.warmup + context.iterations):
            started = time.perf_counter()
            try:
                view = service.create()
            except BackupVerificationError as exc:
                verification_errors.append(str(exc))
                integrity_all_ok = False
                continue
            creates.append((time.perf_counter() - started) * 1000.0)
            started = time.perf_counter()
            verification = service.verify(view.id)
            verifies.append((time.perf_counter() - started) * 1000.0)
            if verification.integrity_check != "ok":
                integrity_all_ok = False
            if verification.sha256 != view.sha256 or verification.byte_size != view.byte_size:
                checksum_all_ok = False
            pointer_count = _artifact_pointer_count(view.path, artifact_root=backup_root / f"{view.id}.artifacts")
            if pointer_count != artifact_count:
                reference_all_ok = False
            wal_rows_in_snapshots.append(_marker_rows_in_backup(view.path))
            wal_files_in_backups.extend(
                sorted(str(path.relative_to(backup_root)) for path in backup_root.rglob("*-wal"))
            )
            wal_files_in_backups.extend(
                sorted(str(path.relative_to(backup_root)) for path in backup_root.rglob("*-shm"))
            )
        listed = service.list_backups()
        list_verified = all(summary.verified for summary in listed)
        writer = _writer_during_backup(
            context,
            db_path=db_path,
            artifact_root=artifact_root,
            backup_root=backup_root,
            project_id=project_id,
            retention=retention,
            operations=writer_ops,
        )
        wal_after = harness.wal_sizes(context.data_root)
    finally:
        engine.dispose()

    if not creates:
        raise FixtureError("BACKUP: không tạo được bản backup nào")
    recorder.record(
        "backup.create",
        creates,
        "ms",
        note="BackupService.create() thật: sqlite3 online backup + integrity_check + copy artifact + sha256 + manifest.",
    )
    recorder.record("backup.verify", verifies, "ms", note="BackupService.verify(): integrity_check + sha256 + kiểm tra con trỏ artifact.")
    recorder.record(
        "backup.writerLatency",
        writer["latencies"],
        "ms",
        note=(
            "Độ trễ commit của writer trên DB nguồn TRONG LÚC backup đang chạy (đo 'user downtime' thay vì "
            "chỉ đo thời gian backup)."
        ),
    )
    recorder.extras["backup"] = {
        "chapters": chapters,
        "artifacts": artifact_count,
        "artifactBytes": payload_bytes,
        "retention": retention,
        "creates": len(creates),
        "createP95Ms": round(percentile(creates, 0.95), 3),
        "verifyP95Ms": round(percentile(verifies, 0.95), 3) if verifies else None,
        "integrityAllOk": integrity_all_ok,
        "checksumAllOk": checksum_all_ok,
        "referenceAllOk": reference_all_ok,
        "verificationErrors": verification_errors,
        "walMarkerRows": wal_marker_count,
        "walMarkerRowsInMainFileOnly": main_copy_rows["markerRowsInMainFile"],
        "walMarkerRowsInBackups": wal_rows_in_snapshots,
        "walFilesCopiedIntoBackups": wal_files_in_backups,
        "walBytesBefore": wal_before["wal"],
        "walBytesAfter": wal_after["wal"],
        "backupsListed": len(listed),
        "backupsAllVerified": list_verified,
        "writerDuringBackup": {key: value for key, value in writer.items() if key != "latencies"},
        "incrementalApi": _backup_incremental_api_probe(),
    }
    recorder.set_measurement(
        "operation",
        (
            f"BackupService thật trên DB tạm {chapters} chương + {artifact_count} artifact "
            f"({payload_bytes} B/artifact): mỗi lượt create() + verify(), và một writer commit liên tục trong lúc "
            "một bản backup khác đang chạy."
        ),
    )
    recorder.note(
        "Không copy live WAL riêng: create() dùng SQLite Online Backup API (đọc qua WAL) chứ không copy file "
        "-wal; bằng chứng là (a) backup root không có file *-wal/*-shm và (b) hàng chỉ nằm trong WAL vẫn có "
        "trong bản backup."
    )
    recorder.set_phase("db", "MEASURED", metric="backup.create")
    recorder.set_phase("queue", "NOT_RUN", note="BACKUP không đi qua worker queue: route/service gọi trực tiếp.")
    _set_provider_phases(recorder)
    recorder.threshold_bool(
        case="Backup — integrity/checksum/reference",
        ok=integrity_all_ok and checksum_all_ok and reference_all_ok and list_verified,
        measured={
            "integrityAllOk": integrity_all_ok,
            "checksumAllOk": checksum_all_ok,
            "referenceAllOk": reference_all_ok,
            "backupsListed": len(listed),
            "backupsAllVerified": list_verified,
            "verificationErrors": verification_errors,
        },
        unit="verify",
        label="integrity_check=ok, sha256/byte_size khớp manifest, con trỏ artifact khớp",
        target=True,
        note=(
            f"{len(creates)} bản backup đã verify qua BackupService.verify() và list_backups(); mỗi bản so "
            "sha256 + byte_size với manifest và đối chiếu toàn bộ con trỏ artifact trong snapshot."
        ),
    )
    recorder.threshold_bool(
        case="Backup — không copy live WAL riêng",
        ok=not wal_files_in_backups and all(value >= wal_marker_count for value in wal_rows_in_snapshots),
        measured={
            "walFilesInBackups": wal_files_in_backups,
            "walMarkerRows": wal_marker_count,
            "walMarkerRowsOnlyInMainFile": main_copy_rows["markerRowsInMainFile"],
            "walMarkerRowsInBackups": wal_rows_in_snapshots,
            "walBytesBefore": wal_before["wal"],
        },
        unit="wal",
        label="không có *-wal/*-shm trong backup; hàng WAL-only vẫn nằm trong snapshot",
        target=True,
        note=(
            "Trước khi backup, hàng đánh dấu được commit vào WAL nhưng KHÔNG checkpoint (file .sqlite3 chính "
            f"không chứa chúng: {main_copy_rows['markerRowsInMainFile']} hàng khi đọc riêng file DB chính, "
            f"WAL {wal_before['wal']} byte). Sau backup, các hàng đó có trong bản backup ⇒ dữ liệu WAL được "
            "đọc qua Online Backup API, không bị bỏ rơi và cũng không copy file WAL."
        ),
    )
    recorder.threshold_bool(
        case="Backup — writer không bị chặn (downtime)",
        ok=bool(writer["allSucceeded"]),
        measured={
            "operations": writer["operations"],
            "succeeded": writer["succeeded"],
            "failed": writer["failed"],
            "p95Ms": writer["p95Ms"],
            "maxMs": writer["maxMs"],
            "backupDurationMs": writer["backupDurationMs"],
        },
        unit="ms",
        label="mọi commit trên DB nguồn trong lúc backup đều thành công (downtime 0)",
        target=writer["operations"],
        note=(
            "Writer thật commit liên tục trên cùng DB nguồn trong lúc một bản backup chạy song song; đo độ trễ "
            "từng commit. Đây là phần 'progress observable' đo được của dòng G-PERF Backup (người dùng vẫn dùng "
            "được app trong lúc backup)."
        ),
    )
    recorder.threshold_bool(
        case="Backup — incremental/progress API",
        ok=bool(_backup_incremental_api_probe()["incrementalOrProgressApi"]),
        measured=_backup_incremental_api_probe(),
        unit="api",
        label="có API incremental/progress quan sát được",
        target=True,
        note=(
            "BackupService.create() chỉ có một chế độ: sqlite3 source.backup(target) một lần cho toàn bộ DB, "
            "không tham số incremental, không callback tiến độ; API công khai của service là create/verify/"
            "list_backups/retention_plan/restore_*. Vì vậy tiêu chí 'incremental/progress' của plan §7 KHÔNG "
            "đạt ở mức API (không hạ ngưỡng): phần đo được là writer không bị chặn trong lúc backup."
        ),
    )


def _commit_wal_only_rows(db_path: Path, *, project_id: str, count: int) -> int:
    """Commit hàng vào WAL mà không checkpoint (không gọi wal_checkpoint)."""

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        for index in range(count):
            connection.execute(
                "INSERT INTO chapters (id, project_id, ordinal, source_title, translated_title, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, 'IMPORTED', ?, ?)",
                (
                    _bench_id("7", index),
                    project_id,
                    90_000 + index,
                    f"WAL-ONLY {index}",
                    datetime.now(UTC).isoformat(),
                    datetime.now(UTC).isoformat(),
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return count


def _rows_in_main_file_only(db_path: Path, data_root: Path) -> dict[str, int]:
    """Đọc riêng file .sqlite3 (không có -wal) để chứng minh hàng đánh dấu chỉ nằm trong WAL."""

    copy_path = data_root / "main-file-only.sqlite3"
    shutil.copyfile(db_path, copy_path)
    connection = sqlite3.connect(copy_path)
    try:
        marker_rows = int(
            connection.execute("SELECT COUNT(*) FROM chapters WHERE source_title LIKE 'WAL-ONLY %'").fetchone()[0]
        )
        total_rows = int(connection.execute("SELECT COUNT(*) FROM chapters").fetchone()[0])
    finally:
        connection.close()
        copy_path.unlink(missing_ok=True)
    return {"markerRowsInMainFile": marker_rows, "totalRowsInMainFile": total_rows}


def _artifact_pointer_count(backup_db: Path, *, artifact_root: Path) -> int:
    connection = sqlite3.connect(backup_db)
    try:
        rows = connection.execute("SELECT relative_path, sha256 FROM artifacts WHERE status != 'DELETED'").fetchall()
    finally:
        connection.close()
    count = 0
    for relative_path, expected_sha256 in rows:
        candidate = artifact_root / str(relative_path)
        if not candidate.is_file():
            alternate = artifact_root / "artifacts" / str(relative_path)
            candidate = alternate if alternate.is_file() else candidate
        if candidate.is_file():
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if actual == str(expected_sha256):
                count += 1
    return count


def _marker_rows_in_backup(backup_db: Path) -> int:
    connection = sqlite3.connect(backup_db)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM chapters WHERE source_title LIKE 'WAL-ONLY %'").fetchone()[0])
    finally:
        connection.close()


def _writer_during_backup(
    context: FixtureContext,
    *,
    db_path: Path,
    artifact_root: Path,
    backup_root: Path,
    project_id: str,
    retention: int,
    operations: int,
) -> dict[str, Any]:
    """Commit trên DB nguồn trong lúc một backup chạy song song (đo downtime người dùng)."""

    service = BackupService(
        db_path=db_path,
        backup_root=backup_root,
        artifact_root=artifact_root,
        retention_count=retention,
        api_lock_path=context.data_root / "studio-api.lock",
        worker_lock_path=context.data_root / "studio-worker.lock",
    )
    latencies: list[float] = []
    failures: list[str] = []
    backup_duration = [0.0]
    done = threading.Event()

    def run_backup() -> None:
        started = time.perf_counter()
        try:
            service.create()
        except Exception as exc:  # noqa: BLE001 - fixture phải ghi lại lỗi thay vì nuốt
            failures.append(f"backup: {type(exc).__name__}: {exc}")
        finally:
            backup_duration[0] = (time.perf_counter() - started) * 1000.0
            done.set()

    thread = threading.Thread(target=run_backup, name="backup-during-writer", daemon=True)
    thread.start()
    succeeded = 0
    connection = sqlite3.connect(db_path, timeout=5.0)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        for index in range(operations):
            started = time.perf_counter()
            try:
                connection.execute(
                    "INSERT INTO chapters (id, project_id, ordinal, source_title, translated_title, state, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, NULL, 'IMPORTED', ?, ?)",
                    (
                        _bench_id("8", index),
                        project_id,
                        95_000 + index,
                        f"WRITER-DURING-BACKUP {index}",
                        datetime.now(UTC).isoformat(),
                        datetime.now(UTC).isoformat(),
                    ),
                )
                connection.commit()
                latencies.append((time.perf_counter() - started) * 1000.0)
                succeeded += 1
            except sqlite3.Error as exc:
                failures.append(f"writer: {type(exc).__name__}: {exc}")
            time.sleep(0.005)
    finally:
        connection.close()
        thread.join(timeout=600)
    if not latencies:
        latencies = [0.0]
    return {
        "latencies": latencies,
        "operations": operations,
        "succeeded": succeeded,
        "failed": len([failure for failure in failures if failure.startswith("writer")]),
        "allSucceeded": succeeded == operations and not [failure for failure in failures if failure.startswith("writer")],
        "failures": failures,
        "p95Ms": round(percentile(latencies, 0.95), 3),
        "maxMs": round(max(latencies), 3),
        "backupDurationMs": round(backup_duration[0], 3),
    }


def _backup_incremental_api_probe() -> dict[str, Any]:
    """Đo bằng chứng API: service có chế độ incremental hay callback tiến độ hay không (đọc chữ ký thật)."""

    import inspect

    create_signature = inspect.signature(BackupService.create)
    init_signature = inspect.signature(BackupService.__init__)
    public_methods = sorted(
        name
        for name, value in inspect.getmembers(BackupService, predicate=inspect.isfunction)
        if not name.startswith("_")
    )
    parameters = sorted(
        name
        for name, parameter in init_signature.parameters.items()
        if name != "self" and parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
    )
    incremental_parameters = [
        name for name in parameters if any(token in name.lower() for token in ("progress", "incremental", "callback"))
    ]
    return {
        "createParameters": sorted(create_signature.parameters),
        "constructorParameters": parameters,
        "incrementalOrProgressParameters": incremental_parameters,
        "publicMethods": public_methods,
        "incrementalOrProgressApi": bool(incremental_parameters),
        "note": "create() nhận tham số nào thì đây là toàn bộ bề mặt điều khiển của nó.",
    }


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

CHLIST = FixtureSpec(
    name="CHLIST",
    title="Chapter list/filter — ≤100 mounted rows, filter/paging qua route thật",
    config={
        "projects": 1,
        "chapters": 1_000,
        "pageSize": 100,
        "clampProbeLimit": 500,
        "pages": 10,
        "marker": "ZZQ-MARKER",
        "markerChapters": 12,
        "dataProfile": "metadata-only",
    },
    runner=run_chlist,
)

CHSWITCH = FixtureSpec(
    name="CHSWITCH",
    title="Đổi chương cached — p95 <200 ms qua 30 lượt, tách cold/warm",
    config={
        "projects": 1,
        "chapters": 1_000,
        "contentChapters": 60,
        "segmentsPerChapter": 20,
        "switches": 30,
        "dataProfile": "translation-metadata",
    },
    runner=run_chswitch,
)

CANCELCKPT = FixtureSpec(
    name="CANCELCKPT",
    title="Cancel sau checkpoint — p95 ≤5 s, thời gian provider báo riêng",
    config={
        "projects": 1,
        "segmentsPerChapter": 12,
        "cancelAfterSegments": 5,
        "dataProfile": "render-fake",
    },
    runner=run_cancelckpt,
)

EDITOR8TAB = FixtureSpec(
    name="EDITOR8TAB",
    title="C2K editor và 8 tab — cap 8 tab/pane, không mất draft khi evict/reopen",
    config={
        "projects": 1,
        "chapters": 1_000,
        "tabs": 8,
        "segmentsPerChapter": 20,
        "editorSegments": 20,
        "dataProfile": "workspace-layout",
    },
    runner=run_editor8tab,
)

SSE30MIN = FixtureSpec(
    name="SSE30MIN",
    title="Phiên SSE rút ngắn — store ≤1.000 metadata, ≤4 frame/s/job",
    config={
        "projects": 1,
        "chapters": 40,
        "jobs": 40,
        "segmentsPerChapter": 6,
        "sessionSeconds": 90,
        "snapshotIntervalMs": 250,
        "dataProfile": "events",
    },
    runner=run_sse30min,
)

BACKUP = FixtureSpec(
    name="BACKUP",
    title="Backup thật trên DB tạm lớn — integrity/checksum/reference, không copy live WAL",
    config={
        "projects": 1,
        "chapters": 2_000,
        "artifacts": 240,
        "payloadBytes": 32_768,
        "retention": 7,
        "writerOps": 40,
        "dataProfile": "backup",
    },
    runner=run_backup,
)

FIXTURES: dict[str, FixtureSpec] = {
    spec.name: spec
    for spec in (S1, S2, C2K, C500, J10K, SSE30, AUDIO500, CHLIST, CHSWITCH, CANCELCKPT, EDITOR8TAB, SSE30MIN, BACKUP)
}


def fixture_names() -> list[str]:
    return list(FIXTURES)
