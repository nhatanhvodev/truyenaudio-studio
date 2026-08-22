from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

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

    return router
