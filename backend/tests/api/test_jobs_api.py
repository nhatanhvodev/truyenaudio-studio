from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.contracts import JobKind, JobStatus, RightsStatus, SourceType
from app.db.models import Job, Project
from app.main import create_app
from app.settings.config import Settings


LOOPBACK_ORIGIN = "http://127.0.0.1:8765"


def test_jobs_snapshot_maps_persisted_jobs_and_after_cursor(settings: Settings, db_session) -> None:
    project = Project(
        id="018f0000-0000-7000-8000-000000000101",
        title="Jobs",
        slug="jobs",
        source_type=SourceType.SELF_AUTHORED.value,
        rights_status=RightsStatus.CLEARED.value,
    )
    first = Job(
        id="018f0000-0000-7000-8000-000000000201",
        kind=JobKind.SYNTHESIZE.value,
        status=JobStatus.RUNNING.value,
        project_id=project.id,
        idempotency_key="job-first",
        progress_current=1,
        progress_total=3,
        updated_at=datetime(2026, 8, 21, 1, 0, tzinfo=UTC),
    )
    second = Job(
        id="018f0000-0000-7000-8000-000000000202",
        kind=JobKind.EXPORT.value,
        status=JobStatus.FAILED.value,
        project_id=project.id,
        idempotency_key="job-second",
        progress_current=2,
        progress_total=2,
        error_code="EXPORT_BLOCKED",
        updated_at=datetime(2026, 8, 21, 1, 1, tzinfo=UTC),
    )
    db_session.add(project)
    db_session.flush()
    db_session.add_all([first, second])
    db_session.commit()

    client = TestClient(create_app(settings=settings, acquire_lock=False), base_url=LOOPBACK_ORIGIN)
    snapshot = client.get("/api/jobs/snapshot").json()["events"]

    assert snapshot == [
        {
            "sequenceId": 1,
            "type": "job",
            "jobId": first.id,
            "status": "RUNNING",
            "current": 1,
            "total": 3,
            "errorCode": None,
        },
        {
            "sequenceId": 2,
            "type": "job",
            "jobId": second.id,
            "status": "FAILED",
            "current": 2,
            "total": 2,
            "errorCode": "EXPORT_BLOCKED",
        },
    ]

    replay = client.get(f"/api/jobs/snapshot?after={snapshot[0]['sequenceId']}").json()["events"]
    assert replay == [snapshot[1]]


def test_jobs_events_replays_after_last_event_id_header(settings: Settings, db_session) -> None:
    project = Project(
        id="018f0000-0000-7000-8000-000000000301",
        title="Jobs SSE",
        slug="jobs-sse",
        source_type=SourceType.SELF_AUTHORED.value,
        rights_status=RightsStatus.CLEARED.value,
    )
    first = Job(
        id="018f0000-0000-7000-8000-000000000401",
        kind=JobKind.SYNTHESIZE.value,
        status=JobStatus.SUCCEEDED.value,
        project_id=project.id,
        idempotency_key="sse-first",
        updated_at=datetime(2026, 8, 21, 2, 0, tzinfo=UTC),
    )
    second = Job(
        id="018f0000-0000-7000-8000-000000000402",
        kind=JobKind.MASTER.value,
        status=JobStatus.RUNNING.value,
        project_id=project.id,
        idempotency_key="sse-second",
        progress_current=4,
        progress_total=8,
        updated_at=datetime(2026, 8, 21, 2, 1, tzinfo=UTC),
    )
    db_session.add(project)
    db_session.flush()
    db_session.add_all([first, second])
    db_session.commit()

    client = TestClient(create_app(settings=settings, acquire_lock=False), base_url=LOOPBACK_ORIGIN)
    after = "1"

    response = client.get("/api/jobs/events", headers={"Last-Event-ID": after})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert second.id in response.text
    assert first.id not in response.text
    assert "2026-08-21T02:00:00+00:00" not in response.text
