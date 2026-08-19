from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.settings.config import Settings


HEARTBEAT_STALE_SECONDS = 90


@dataclass(frozen=True)
class ReadinessProbe:
    data_root: Path
    database_path: Path
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    executable_resolver: Callable[[str], str | None] = shutil.which
    executable_probe: Callable[[str], bool] | None = None
    sqlite_probe: Callable[[], bool] | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> ReadinessProbe:
        return cls(data_root=settings.data_root, database_path=settings.data_root / "studio.sqlite3")

    def evaluate(self) -> dict[str, object]:
        components = {
            "api": {"status": "ok", "message": "live"},
            "sqlite": self._sqlite_component(),
            "worker": self._worker_component(),
            "storage": self._storage_component(),
            "ffmpeg": self._ffmpeg_component(),
            "providers": {"status": "info", "message": "provider/model unconfigured"},
        }
        ready = all(
            component["status"] in {"ok", "info"}
            for component in components.values()
            if isinstance(component, dict)
        )
        return {"status": "ready" if ready else "not_ready", "components": components}

    def _sqlite_component(self) -> dict[str, str]:
        ok = self.sqlite_probe() if self.sqlite_probe is not None else _sqlite_write_probe(self.database_path)
        return {"status": "ok" if ok else "fail", "message": "writable" if ok else "write probe failed"}

    def _worker_component(self) -> dict[str, str]:
        heartbeat_path = self.data_root / "worker-heartbeat.json"
        try:
            raw = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            updated_at = datetime.fromisoformat(str(raw["updated_at"]))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return {"status": "missing", "message": "worker heartbeat missing"}

        age_seconds = (self.clock() - updated_at.astimezone(UTC)).total_seconds()
        if age_seconds > HEARTBEAT_STALE_SECONDS:
            return {"status": "stale", "message": "worker heartbeat stale"}
        return {"status": "ok", "message": str(raw.get("status", "fresh"))}

    def _storage_component(self) -> dict[str, str]:
        if not _probe_directory(self.data_root):
            return {"status": "fail", "message": "data root unwritable"}
        temp_root = self.data_root / "temp"
        if not _probe_directory(temp_root):
            return {"status": "fail", "message": "temp unwritable"}
        return {"status": "ok", "message": "data and temp writable"}

    def _ffmpeg_component(self) -> dict[str, str]:
        ffmpeg = self.executable_resolver("ffmpeg")
        ffprobe = self.executable_resolver("ffprobe")
        if ffmpeg is None or ffprobe is None:
            return {"status": "missing", "message": "ffmpeg or ffprobe missing"}
        checker = self.executable_probe or _can_run_version
        if not checker(ffmpeg) or not checker(ffprobe):
            return {"status": "missing", "message": "ffmpeg or ffprobe unavailable"}
        return {"status": "ok", "message": "available"}


def create_health_router(probe: ReadinessProbe | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/health")
    active_probe = probe or ReadinessProbe.from_settings(Settings())

    @router.get("/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @router.get("/ready")
    def ready() -> JSONResponse:
        body = active_probe.evaluate()
        return JSONResponse(body, status_code=200 if body["status"] == "ready" else 503)

    return router


def _sqlite_write_probe(database_path: Path) -> bool:
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE __health_write_probe(id INTEGER)")
            connection.execute("INSERT INTO __health_write_probe(id) VALUES (1)")
            connection.rollback()
        return True
    except sqlite3.Error:
        return False


def _probe_directory(directory: Path) -> bool:
    probe_path = directory / f".health-{uuid4().hex}.tmp"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(probe_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "wb") as file:
            file.write(b"ok")
            file.flush()
            os.fsync(file.fileno())
        return True
    except OSError:
        return False
    finally:
        probe_path.unlink(missing_ok=True)


def _can_run_version(executable: str) -> bool:
    try:
        completed = subprocess.run(
            [executable, "-version"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0
