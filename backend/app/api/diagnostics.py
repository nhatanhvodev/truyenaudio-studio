from __future__ import annotations

import hashlib
import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import text
from pydantic import BaseModel, ConfigDict, Field

from app.db.base import create_engine_for
from app.modules.diagnostics.service import DiagnosticsService, sha256_bytes
from app.settings.config import Settings


class DiagnosticExportRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    include_sample: bool = Field(default=False, alias="includeSample")
    sample_text: str | None = Field(default=None, alias="sampleText")


def create_diagnostics_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/diagnostics")
    active_settings = settings or Settings()

    @router.get("/health")
    def health() -> dict[str, object]:
        return DiagnosticsService(active_settings).health_snapshot()

    @router.post("/export")
    def export(request: DiagnosticExportRequest) -> Response:
        payload = DiagnosticsService(active_settings).export_zip(
            include_sample=request.include_sample,
            sample_text=request.sample_text,
        )
        return Response(
            payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="truyenaudio-diagnostics.zip"',
                "X-Diagnostics-Sha256": sha256_bytes(payload),
            },
        )

    @router.get("/fake-recovery/check")
    def fake_recovery_check(project_id: str = Query(alias="projectId")) -> dict[str, object]:
        if os.getenv("STUDIO_FAKE_AUDIO") != "1":
            raise HTTPException(status_code=404, detail="not found")
        return _fake_recovery_check(active_settings, project_id)

    return router


def _fake_recovery_check(settings: Settings, project_id: str) -> dict[str, object]:
    engine = create_engine_for(settings.data_root / "studio.sqlite3")
    try:
        with engine.connect() as connection:
            duplicate_cache_keys = [
                _digest_key(row["kind"], row["input_hash"], row["settings_hash"])
                for row in connection.execute(
                    text(
                        """
                        SELECT artifacts.kind, artifacts.input_hash, artifacts.settings_hash, COUNT(*) AS count
                        FROM artifacts
                        JOIN chapters ON chapters.id = artifacts.chapter_id
                        WHERE chapters.project_id = :project_id
                          AND artifacts.status = 'READY'
                        GROUP BY artifacts.kind, artifacts.input_hash, artifacts.settings_hash
                        HAVING COUNT(*) > 1
                        ORDER BY artifacts.kind, artifacts.input_hash, artifacts.settings_hash
                        """
                    ),
                    {"project_id": project_id},
                ).mappings()
            ]
            duplicate_export_manifests = [
                _digest_key(row["chapter_id"], row["kind"], row["manifest_sha256"])
                for row in connection.execute(
                    text(
                        """
                        SELECT exports.chapter_id, exports.kind, exports.manifest_sha256, COUNT(*) AS count
                        FROM exports
                        JOIN chapters ON chapters.id = exports.chapter_id
                        WHERE chapters.project_id = :project_id
                          AND exports.status = 'READY'
                          AND exports.manifest_sha256 IS NOT NULL
                        GROUP BY exports.chapter_id, exports.kind, exports.manifest_sha256
                        HAVING COUNT(*) > 1
                        ORDER BY exports.chapter_id, exports.kind, exports.manifest_sha256
                        """
                    ),
                    {"project_id": project_id},
                ).mappings()
            ]
            artifact_paths = [
                str(row["relative_path"])
                for row in connection.execute(
                    text(
                        """
                        SELECT artifacts.relative_path
                        FROM artifacts
                        JOIN chapters ON chapters.id = artifacts.chapter_id
                        WHERE chapters.project_id = :project_id
                          AND artifacts.status = 'READY'
                        """
                    ),
                    {"project_id": project_id},
                ).mappings()
            ]
    finally:
        engine.dispose()

    roots = (settings.data_root, settings.data_root / "artifacts")
    missing_count = sum(1 for relative_path in artifact_paths if not any((root / relative_path).is_file() for root in roots))
    return {
        "duplicateReadyCacheKeys": duplicate_cache_keys,
        "duplicateReadyExportManifests": duplicate_export_manifests,
        "missingReadyArtifactCount": missing_count,
    }


def _digest_key(*parts: object) -> str:
    return hashlib.sha256(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()
