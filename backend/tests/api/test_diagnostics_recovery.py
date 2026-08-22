from __future__ import annotations

from fastapi.testclient import TestClient

from app.contracts import RightsStatus, SourceType
from app.db.models import Project
from app.main import create_app
from app.settings.config import Settings


LOOPBACK_ORIGIN = "http://127.0.0.1:8765"


def test_fake_recovery_run_uses_worker_recovery_path(
    settings: Settings,
    db_session,
    monkeypatch,
) -> None:
    monkeypatch.setenv("STUDIO_FAKE_AUDIO", "1")
    project = Project(
        id="018f0000-0000-7000-8000-000000009001",
        title="Recovery endpoint",
        slug="recovery-endpoint",
        source_type=SourceType.SELF_AUTHORED.value,
        rights_status=RightsStatus.CLEARED.value,
    )
    db_session.add(project)
    db_session.commit()
    client = TestClient(create_app(settings=settings, acquire_lock=False), base_url=LOOPBACK_ORIGIN)
    token = client.get("/api/security/bootstrap").json()["csrfToken"]

    response = client.post(
        f"/api/diagnostics/fake-recovery/run?projectId={project.id}",
        headers={"Origin": LOOPBACK_ORIGIN, "X-CSRF-Token": token},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workerRunCount"] >= 2
    assert body["workerDrivenRecoveryCount"] == 1
    assert body["recoveredJobStatus"] == "SUCCEEDED"
    assert body["duplicateReadyCacheKeysBefore"] == []
    assert body["duplicateReadyCacheKeysAfter"] == []
    assert body["duplicateReadyExportManifestsBefore"] == []
    assert body["duplicateReadyExportManifestsAfter"] == []
    assert body["missingReadyArtifactCountAfter"] == 0
