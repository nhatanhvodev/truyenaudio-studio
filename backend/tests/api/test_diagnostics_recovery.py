from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from app.contracts import ChapterState, ImportKind, JobKind, RightsStatus, SourceType
from app.db.models import Chapter, Project, SourceRevision
from app.main import create_app
from app.modules.jobs.runner import JobRunner
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
    chapter = Chapter(
        id="018f0000-0000-7000-8000-000000009101",
        project_id=project.id,
        ordinal=1,
        source_title="Recovery chapter",
        state=ChapterState.NORMALIZED.value,
    )
    source_text = "第1章\n林动说：你好。"
    revision = SourceRevision(
        id="018f0000-0000-7000-8000-000000009102",
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text=source_text,
        normalized_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        han_char_count=6,
        total_char_count=len(source_text),
        normalizer_version="test-v1",
    )
    db_session.add(chapter)
    db_session.add(revision)
    db_session.flush()
    chapter.active_source_revision_id = revision.id
    db_session.commit()
    job = JobRunner(db_session.get_bind()).enqueue(
        JobKind.EXPORT,
        project.id,
        chapter.id,
        "diagnostics-real-batch-recovery",
    )
    client = TestClient(create_app(settings=settings, acquire_lock=False), base_url=LOOPBACK_ORIGIN)
    token = client.get("/api/security/bootstrap").json()["csrfToken"]

    response = client.post(
        f"/api/diagnostics/fake-recovery/run?projectId={project.id}&chapterId={chapter.id}&jobId={job.id}",
        headers={"Origin": LOOPBACK_ORIGIN, "X-CSRF-Token": token},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["workerRunCount"] >= 2
    assert body["workerDrivenRecoveryCount"] == 1
    assert body["recoveredJobId"] == job.id
    assert body["recoveredProjectId"] == project.id
    assert body["recoveredChapterId"] == chapter.id
    assert body["recoveredJobKind"] == "EXPORT"
    assert body["recoveredJobStatus"] == "SUCCEEDED"
    assert body["duplicateReadyCacheKeysBefore"] == []
    assert body["duplicateReadyCacheKeysAfter"] == []
    assert body["duplicateReadyExportManifestsBefore"] == []
    assert body["duplicateReadyExportManifestsAfter"] == []
    assert body["missingReadyArtifactCountAfter"] == 0
