from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.contracts import EvidenceKind, RightsScope
from app.db.base import create_engine_for, session_factory
from app.modules.artifacts.store import ArtifactStore
from app.modules.compliance.evidence import CreateGrant, EvidenceService, EvidenceUpload, GrantService
from app.settings.config import Settings


class CreateGrantRequest(BaseModel):
    scope: RightsScope
    territory: str
    allows_ai_processing: bool
    allows_third_party_cloud: bool
    valid_from: datetime
    expires_at: datetime | None = None
    evidence_id: str | None = None


def create_rights_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/rights")
    active_settings = settings or Settings()

    def services() -> Iterator[tuple[EvidenceService, GrantService]]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield EvidenceService(session, ArtifactStore(active_settings.data_root)), GrantService(session)
        engine.dispose()

    @router.post("/evidence")
    async def create_evidence(
        project_id: str,
        file: UploadFile = File(...),
        evidence_kind: EvidenceKind = Form(...),
        issuer: str | None = Form(default=None),
        expires_at: datetime | None = Form(default=None),
        pair: tuple[EvidenceService, GrantService] = Depends(services),
    ) -> dict[str, object]:
        evidence_service, _ = pair
        try:
            evidence = evidence_service.store(
                project_id,
                EvidenceUpload(
                    file.filename or "evidence.txt",
                    await file.read(),
                    evidence_kind,
                    issuer,
                    expires_at,
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _dataclass_dict(evidence)

    @router.get("/evidence")
    def list_evidence(
        project_id: str,
        pair: tuple[EvidenceService, GrantService] = Depends(services),
    ) -> dict[str, object]:
        evidence_service, _ = pair
        return {"evidence": [_dataclass_dict(evidence) for evidence in evidence_service.list(project_id)]}

    @router.post("/grants")
    def create_grant(
        project_id: str,
        request: CreateGrantRequest,
        pair: tuple[EvidenceService, GrantService] = Depends(services),
    ) -> dict[str, object]:
        _, grant_service = pair
        try:
            grant = grant_service.create(
                project_id,
                CreateGrant(
                    request.scope,
                    request.territory,
                    request.allows_ai_processing,
                    request.allows_third_party_cloud,
                    request.valid_from,
                    request.expires_at,
                    request.evidence_id,
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _dataclass_dict(grant)

    @router.get("/grants")
    def list_grants(
        project_id: str,
        pair: tuple[EvidenceService, GrantService] = Depends(services),
    ) -> dict[str, object]:
        _, grant_service = pair
        return {"grants": [_dataclass_dict(grant) for grant in grant_service.list(project_id)]}

    return router


def _dataclass_dict(value: object) -> dict[str, object]:
    from dataclasses import asdict
    from datetime import datetime
    from enum import Enum

    def convert(item: object) -> object:
        if isinstance(item, Enum):
            return item.name
        if isinstance(item, datetime):
            return item.isoformat()
        return item

    return {key: convert(item) for key, item in asdict(value).items()}
