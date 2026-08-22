from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import zipfile

from sqlalchemy import text

from app.db.base import create_engine_for
from app.modules.diagnostics.logging import scrub_text
from app.settings.config import Settings


class DiagnosticsService:
    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        executable_resolver: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.executable_resolver = executable_resolver
        self.log_dir = settings.data_root / "diagnostics" / "logs"

    def health_snapshot(self) -> dict[str, object]:
        sqlite_status = self._sqlite_snapshot()
        ffmpeg_status = self._binary_snapshot("ffmpeg")
        ffprobe_status = self._binary_snapshot("ffprobe")
        disk = self._disk_snapshot()
        worker = self._worker_snapshot()
        models = self._model_snapshot()
        ready = (
            sqlite_status["status"] == "ok"
            and disk["status"] == "ok"
            and worker["status"] in {"ok", "missing", "stale"}
        )
        return {
            "timestamp": self.clock().astimezone(UTC).isoformat(),
            "status": "ready" if ready else "not_ready",
            "components": {
                "api": {"status": "ok", "message": "live"},
                "worker": worker,
                "sqlite": sqlite_status,
                "ffmpeg": ffmpeg_status,
                "ffprobe": ffprobe_status,
                "models": models,
                "disk": disk,
                "providers": {"status": "info", "message": "provider availability does not gate local API readiness"},
            },
        }

    def export_zip(self, *, include_sample: bool = False, sample_text: str | None = None) -> bytes:
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("config.json", _json_bytes(self._safe_config()))
            zf.writestr("version.json", _json_bytes(self._version()))
            zf.writestr("health.json", _json_bytes(self.health_snapshot()))
            for path in sorted(self.log_dir.glob("diagnostics-*.jsonl"))[-30:]:
                zf.writestr(f"logs/{path.name}", self._redacted_log_text(path))
            if include_sample and sample_text:
                zf.writestr("samples/user-selected-sample.txt", scrub_text(sample_text))
        return archive.getvalue()

    def _safe_config(self) -> dict[str, object]:
        return {
            "host": self.settings.host,
            "port": self.settings.port,
            "workerConcurrency": self.settings.worker_concurrency,
            "dataRootName": self.settings.data_root.name,
            "diagnosticsLogRetentionDays": 30,
        }

    def _version(self) -> dict[str, object]:
        return {"app": "truyenaudio-studio", "diagnosticsSchema": "diagnostics-v1"}

    def _worker_snapshot(self) -> dict[str, object]:
        heartbeat_path = self.settings.data_root / "worker-heartbeat.json"
        try:
            heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))
            updated_at = datetime.fromisoformat(str(heartbeat["updated_at"])).astimezone(UTC)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return {"status": "missing", "message": "worker heartbeat missing"}
        return {
            "status": "ok",
            "message": str(heartbeat.get("status", "heartbeat")),
            "updatedAt": updated_at.isoformat(),
            "workerId": heartbeat.get("worker_id"),
        }

    def _sqlite_snapshot(self) -> dict[str, object]:
        database_path = self.settings.data_root / "studio.sqlite3"
        try:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(database_path) as connection:
                journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.Error:
            return {"status": "fail", "message": "sqlite probe failed"}
        return {
            "status": "ok" if integrity == "ok" else "fail",
            "message": "integrity ok" if integrity == "ok" else "integrity check failed",
            "pragma": {"journalMode": journal_mode, "foreignKeys": bool(foreign_keys)},
            "integrity": integrity,
        }

    def _binary_snapshot(self, name: str) -> dict[str, object]:
        executable = self.executable_resolver(name)
        if executable is None:
            return {"status": "missing", "message": f"{name} missing"}
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
            return {"status": "missing", "message": f"{name} unavailable"}
        first_line = completed.stdout.splitlines()[0] if completed.stdout.splitlines() else ""
        return {"status": "ok" if completed.returncode == 0 else "missing", "message": scrub_text(first_line[:120])}

    def _disk_snapshot(self) -> dict[str, object]:
        usage = shutil.disk_usage(self.settings.data_root)
        return {"status": "ok", "freeBytes": usage.free, "totalBytes": usage.total}

    def _model_snapshot(self) -> dict[str, object]:
        database_path = self.settings.data_root / "studio.sqlite3"
        if not database_path.exists():
            return {"status": "info", "models": []}
        engine = create_engine_for(database_path)
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        """
                        SELECT id, name, locale, model_snapshot_hash, license_snapshot_artifact_id
                        FROM voice_presets
                        ORDER BY updated_at DESC, id ASC
                        LIMIT 50
                        """
                    )
                ).mappings().all()
        except Exception:
            return {"status": "info", "models": []}
        finally:
            engine.dispose()
        return {
            "status": "ok",
            "models": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "locale": row["locale"],
                    "modelSnapshotHash": row["model_snapshot_hash"],
                    "licenseSnapshotArtifactId": row["license_snapshot_artifact_id"],
                }
                for row in rows
            ],
        }

    def _redacted_log_text(self, path: Path) -> str:
        lines: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                lines.append(scrub_text(line))
                continue
            lines.append(json.dumps(_scrub_json(payload), ensure_ascii=False, separators=(",", ":")))
        return "\n".join(lines) + ("\n" if lines else "")


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def _scrub_json(value: object) -> object:
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        return [_scrub_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_json(item) for key, item in value.items()}
    return value


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
