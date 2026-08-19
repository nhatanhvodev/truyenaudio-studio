from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter

from app.modules.artifacts.store import ArtifactStore, UnsafeArtifactPath
from app.settings.config import Settings


def create_poc_router(data_root: Path | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/poc")
    root = data_root or Settings().data_root

    @router.get("/status")
    def status() -> dict[str, object]:
        return read_poc_status(root)

    return router


def read_poc_status(data_root: Path) -> dict[str, object]:
    report_dir = data_root / "projects" / "poc"
    if not report_dir.exists():
        return {"status": "not_run"}

    reports = sorted(report_dir.glob("poc-report-*.json"), key=lambda path: path.name, reverse=True)
    if not reports:
        return {"status": "not_run"}

    store = ArtifactStore(data_root)
    for report_path in reports:
        try:
            relative_path = report_path.relative_to(data_root).as_posix()
            store.resolve(relative_path)
        except (ValueError, UnsafeArtifactPath):
            continue
        checksum_path = Path(str(report_path) + ".sha256")
        if not checksum_path.is_file():
            return {"status": "invalid", "reason": "checksum_missing"}
        payload = report_path.read_bytes()
        actual = hashlib.sha256(payload).hexdigest()
        expected = _read_checksum(checksum_path, report_path.name)
        if expected != actual:
            return {"status": "invalid", "reason": "checksum_mismatch"}
        try:
            report = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"status": "invalid", "reason": "report_json_invalid"}
        return {
            "status": "ready",
            "generated_at": report.get("generated_at"),
            "provider": report.get("provider"),
            "report_sha256": actual,
            "gates": _public_gates(report.get("gates")),
        }

    return {"status": "invalid", "reason": "no_contained_report"}


def _read_checksum(path: Path, report_name: str) -> str | None:
    try:
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return None
    parts = first_line.split()
    if len(parts) < 2 or parts[1] != report_name:
        return None
    return parts[0]


def _public_gates(value: object) -> object:
    if not isinstance(value, dict):
        return {}
    return value
