"""Hạ tầng đo cho harness benchmark offline (task V01).

Cung cấp: data root tạm + `alembic upgrade head`, thông tin môi trường, đếm câu SQL
và tách thời gian trong DB khỏi thời gian Python/HTTP, đo RSS và kích thước WAL.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
import gc
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psutil
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, event

from scripts.benchmarks import BACKEND_DIR
from scripts.benchmarks.stats import summarize

__all__ = [
    "SqlPhaseCounter",
    "bootstrap_database",
    "database_path",
    "environment_info",
    "gc_collect",
    "migrate_database",
    "rss_mb",
    "temp_data_root",
    "wal_sizes",
]

DATABASE_NAME = "studio.sqlite3"


class BenchmarkError(RuntimeError):
    """Lỗi hạ tầng của harness (không phải FAIL ngưỡng)."""


def database_path(data_root: Path) -> Path:
    return Path(data_root) / DATABASE_NAME


@contextmanager
def temp_data_root(*, prefix: str = "studio-bench-", keep: bool = False) -> Iterator[Path]:
    """Data root TẠM dưới thư mục temp của hệ điều hành.

    Không bao giờ trỏ vào data root thật của studio: người dùng chỉ định được thư mục
    cha qua tham số, và mặc định là tempdir của hệ điều hành.
    """

    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield path
    finally:
        if keep:
            message = f"[benchmark] giữ lại data root tạm: {path}"
            print(message)
        else:
            shutil.rmtree(path, ignore_errors=True)


def migrate_database(data_root: Path) -> None:
    data_root = Path(data_root)
    data_root.mkdir(parents=True, exist_ok=True)
    db_path = database_path(data_root)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    command.upgrade(config, "head")


def bootstrap_database(data_root: Path) -> Engine:
    """Tạo DB tạm đã migrate head rồi trả engine mở trên đó."""

    from app.db.base import create_engine_for

    migrate_database(data_root)
    return create_engine_for(database_path(data_root))


def wal_sizes(data_root: Path) -> dict[str, object]:
    data_root = Path(data_root)
    sizes: dict[str, int] = {}
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{database_path(data_root)}{suffix}")
        sizes[f"studio.sqlite3{suffix}"] = candidate.stat().st_size if candidate.is_file() else 0
    return {
        "wal": sizes["studio.sqlite3-wal"],
        "shm": sizes["studio.sqlite3-shm"],
        "database": sizes["studio.sqlite3"],
        "files": sizes,
        "note": "Kích thước file sau khi đo xong (WAL của chế độ journal_mode=WAL).",
    }


def rss_mb() -> float:
    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


def gc_collect() -> None:
    gc.collect()


def _node_version() -> str | None:
    try:
        completed = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    version = completed.stdout.strip()
    return version or None


def environment_info() -> dict[str, object]:
    uname = platform.uname()
    memory = psutil.virtual_memory()
    return {
        "os": f"{uname.system} {uname.release} ({uname.version})",
        "platform": platform.platform(),
        "cpu": uname.processor or platform.processor() or "unknown",
        "cpuCount": psutil.cpu_count(logical=True),
        "cpuCountPhysical": psutil.cpu_count(logical=False),
        "machine": uname.machine,
        "ramTotalMb": round(memory.total / (1024 * 1024), 1),
        "ramAvailableMb": round(memory.available / (1024 * 1024), 1),
        "ramUsedMb": round(memory.used / (1024 * 1024), 1),
        "pythonVersion": platform.python_version(),
        "pythonImplementation": platform.python_implementation(),
        "nodeVersion": _node_version(),
        "capturedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


@dataclass
class SqlPhaseCounter:
    """Đếm số câu SQL và tổng thời gian trong DB cho từng thao tác.

    Listener gắn ở mức lớp `Engine` nên bắt được cả những engine mà route FastAPI tạo
    mới cho mỗi request (đúng hành vi của app: mỗi request một engine).
    """

    statements: int = 0
    seconds: float = 0.0
    _local: threading.local = field(default_factory=threading.local, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _attached: bool = field(default=False, repr=False)

    def attach(self) -> SqlPhaseCounter:
        if self._attached:
            return self
        event.listen(Engine, "before_cursor_execute", self._before)
        event.listen(Engine, "after_cursor_execute", self._after)
        self._attached = True
        return self

    def detach(self) -> None:
        if not self._attached:
            return
        event.remove(Engine, "before_cursor_execute", self._before)
        event.remove(Engine, "after_cursor_execute", self._after)
        self._attached = False

    def begin_op(self) -> dict[str, float]:
        """Mốc bắt đầu một thao tác; trả token để `end_op` tính chênh lệch."""

        with self._lock:
            token = {
                "seconds": self.seconds,
                "statements": float(self.statements),
                "threadSeconds": float(getattr(self._local, "seconds", 0.0)),
                "threadStatements": float(getattr(self._local, "statements", 0)),
            }
        self._local.pending = {}
        return token

    def end_op(self, token: dict[str, float]) -> tuple[float, int, str]:
        """Trả (giây trong DB, số câu SQL, cách quy kết) cho thao tác vừa chạy.

        - `thread-local`: SQL chạy ngay trên thread gọi thao tác (worker claim, gọi
          Service trực tiếp) — số đo chính xác cho riêng thao tác đó;
        - `process-global-delta`: SQL chạy ở thread khác (TestClient/httpx chạy ASGI
          app trong worker thread), nên lấy chênh lệch bộ đếm toàn tiến trình. Với
          fixture chạy tuần tự một thao tác một lượt, chênh lệch này chính là thao tác.
        """

        thread_seconds = float(getattr(self._local, "seconds", 0.0)) - token["threadSeconds"]
        thread_statements = float(getattr(self._local, "statements", 0)) - token["threadStatements"]
        with self._lock:
            global_seconds = self.seconds - token["seconds"]
            global_statements = float(self.statements) - token["statements"]
        if thread_statements <= 0 < global_statements:
            return global_seconds, int(global_statements), "process-global-delta"
        return thread_seconds, int(thread_statements), "thread-local"

    def _before(self, conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        pending = getattr(self._local, "pending", None)
        if pending is None:
            pending = {}
            self._local.pending = pending
        pending[id(cursor)] = (time.perf_counter(), _statement_scale(executemany, parameters))

    def _after(self, conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
        pending = getattr(self._local, "pending", None)
        record = pending.pop(id(cursor), None) if isinstance(pending, dict) else None
        if record is None:
            return
        started, scale = record
        elapsed = time.perf_counter() - started
        self._local.seconds = float(getattr(self._local, "seconds", 0.0)) + elapsed
        self._local.statements = int(getattr(self._local, "statements", 0)) + scale
        with self._lock:
            self.seconds += elapsed
            self.statements += scale

    def summary(self) -> dict[str, object]:
        return {
            "statements": self.statements,
            "seconds": round(self.seconds, 3),
            "note": "Tổng theo cả tiến trình; số câu SQL mỗi thao tác nằm trong metrics/<label>.statements.",
            "attribution": (
                "thread-local khi SQL chạy trên thread gọi thao tác; process-global-delta khi "
                "TestClient chạy app ở thread khác (fixture tuần tự nên chênh lệch = thao tác)."
            ),
        }


def _statement_scale(executemany: bool, parameters: Any) -> int:
    """Một lệnh executemany đếm theo số dòng tham số (số câu SQL thực thi)."""

    if not executemany:
        return 1
    if isinstance(parameters, (list, tuple)):
        return max(1, len(parameters))
    return 1


def summarize_samples(samples: list[float], unit: str, *, digits: int = 3) -> dict[str, object]:
    return summarize(samples, unit, digits=digits)

