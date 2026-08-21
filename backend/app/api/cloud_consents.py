from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.contracts import ArtifactKind, ArtifactStatus, CloudConsentStatus, EvidenceKind, new_id
from app.db.base import create_engine_for, session_factory, utc_now
from app.db.models import Artifact, CloudProcessingConsent, Project, RightsEvidence
from app.modules.artifacts.store import ArtifactStore, ArtifactWrite
from app.settings.config import Settings


class GrantCloudConsentRequest(BaseModel):
    provider_profile_id: str
    policy_text: str
    accepted_policy_sha256: str
    attestation_text: str


@dataclass(frozen=True)
class CloudConsentView:
    id: str
    project_id: str
    provider_profile_id: str | None
    status: str
    policy_snapshot_artifact_id: str | None
    attestation_evidence_id: str | None
    accepted_at: datetime | None
    revoked_at: datetime | None


def create_cloud_consents_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/projects/{project_id}/cloud-consents")
    active_settings = settings or Settings()

    def service_dependency() -> Iterator[CloudConsentService]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield CloudConsentService(session, ArtifactStore(active_settings.data_root))
        engine.dispose()

    @router.post("")
    def grant_consent(
        project_id: str,
        request: GrantCloudConsentRequest,
        service: CloudConsentService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            return _view_dict(service.grant(project_id, request))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/{consent_id}/revoke")
    def revoke_consent(
        project_id: str,
        consent_id: str,
        service: CloudConsentService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            return _view_dict(service.revoke(project_id, consent_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


class CloudConsentService:
    def __init__(self, session: Session, artifact_store: ArtifactStore) -> None:
        self.session = session
        self.artifact_store = artifact_store

    def grant(self, project_id: str, request: GrantCloudConsentRequest) -> CloudConsentView:
        project = self.session.get(Project, project_id)
        if project is None:
            raise ValueError("PROJECT_NOT_FOUND")
        policy_bytes = request.policy_text.encode("utf-8")
        policy_sha256 = hashlib.sha256(policy_bytes).hexdigest()
        if policy_sha256 != request.accepted_policy_sha256:
            raise ValueError("POLICY_HASH_MISMATCH")

        policy_artifact_id = self._store_artifact(
            project,
            ArtifactKind.LICENSE_SNAPSHOT,
            "cloud-policy",
            "policy.json",
            policy_bytes,
            {"provider_profile_id": request.provider_profile_id, "policy_sha256": policy_sha256},
        )
        attestation_evidence_id = self._store_attestation(project, request.attestation_text.encode("utf-8"))
        now = utc_now()
        consent = CloudProcessingConsent(
            id=new_id(),
            project_id=project_id,
            provider_profile_id=request.provider_profile_id,
            status=CloudConsentStatus.GRANTED.value,
            attestation_evidence_id=attestation_evidence_id,
            policy_snapshot_artifact_id=policy_artifact_id,
            accepted_at=now,
        )
        self.session.add(consent)
        self.session.commit()
        return _view(consent)

    def revoke(self, project_id: str, consent_id: str) -> CloudConsentView:
        consent = self.session.get(CloudProcessingConsent, consent_id)
        if consent is None or consent.project_id != project_id:
            raise ValueError("CONSENT_NOT_FOUND")
        consent.status = CloudConsentStatus.REVOKED.value
        consent.revoked_at = utc_now()
        self.session.commit()
        return _view(consent)

    def _store_attestation(self, project: Project, payload: bytes) -> str:
        artifact_id = self._store_artifact(
            project,
            ArtifactKind.CONSENT_EVIDENCE,
            "cloud-consent",
            "attestation.txt",
            payload,
            {"project_id": project.id},
        )
        artifact = self.session.get(Artifact, artifact_id)
        if artifact is None:
            raise ValueError("CONSENT_ARTIFACT_MISSING")
        evidence = RightsEvidence(
            id=new_id(),
            project_id=project.id,
            evidence_kind=EvidenceKind.USER_ATTESTATION.value,
            display_name="cloud-attestation.txt",
            relative_path=artifact.relative_path,
            sha256=artifact.sha256,
            notes="Cloud processing consent evidence only; not a publication grant.",
        )
        self.session.add(evidence)
        self.session.flush()
        return evidence.id

    def _store_artifact(
        self,
        project: Project,
        kind: ArtifactKind,
        folder: str,
        filename: str,
        payload: bytes,
        metadata: dict[str, object],
    ) -> str:
        input_hash = hashlib.sha256(payload).hexdigest()
        settings_hash = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode("utf-8")).hexdigest()
        artifact_id = new_id()
        write = ArtifactWrite(
            kind=kind,
            relative_path=f"projects/{project.slug}/{folder}/{artifact_id}-{filename}",
            input_hash=input_hash,
            settings_hash=settings_hash,
            mime_type="text/plain; charset=utf-8" if filename.endswith(".txt") else "application/json",
        )
        with self.artifact_store.begin(write) as writer:
            writer.file.write(payload)
            stored = writer.commit()
        artifact = Artifact(
            id=artifact_id,
            kind=kind.value,
            status=ArtifactStatus.READY.value,
            relative_path=stored.relative_path,
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            mime_type=write.mime_type,
            input_hash=write.input_hash,
            settings_hash=write.settings_hash,
            producer="CloudConsentService",
            producer_version="task3",
            metadata_json=metadata,
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact.id


def _view(consent: CloudProcessingConsent) -> CloudConsentView:
    return CloudConsentView(
        id=consent.id,
        project_id=consent.project_id,
        provider_profile_id=consent.provider_profile_id,
        status=consent.status,
        policy_snapshot_artifact_id=consent.policy_snapshot_artifact_id,
        attestation_evidence_id=consent.attestation_evidence_id,
        accepted_at=consent.accepted_at,
        revoked_at=consent.revoked_at,
    )


def _view_dict(view: CloudConsentView) -> dict[str, object]:
    data = asdict(view)
    for key in ("accepted_at", "revoked_at"):
        if isinstance(data[key], datetime):
            data[key] = data[key].isoformat()
    return data
