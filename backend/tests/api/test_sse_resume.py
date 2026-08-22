from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sse_starlette.sse import AppStatus

from app.contracts import JobKind, JobStatus, RightsStatus, SourceType
from app.db.models import Job, Project
from app.main import create_app
from app.settings.config import Settings


LOOPBACK_ORIGIN = "http://127.0.0.1:8765"


def test_events_reconnect_starts_after_numeric_sequence_id(settings: Settings, db_session) -> None:
    project = Project(
        id="018f0000-0000-7000-8000-000000007001",
        title="SSE",
        slug="sse",
        source_type=SourceType.SELF_AUTHORED.value,
        rights_status=RightsStatus.CLEARED.value,
    )
    db_session.add(project)
    db_session.flush()
    for index, status in enumerate(
        [JobStatus.QUEUED.value, JobStatus.RUNNING.value, JobStatus.SUCCEEDED.value, JobStatus.FAILED.value],
        start=1,
    ):
        db_session.add(
            Job(
                id=f"018f0000-0000-7000-8000-00000000710{index}",
                kind=JobKind.TRANSLATE.value,
                status=status,
                project_id=project.id,
                idempotency_key=f"sse-{index}",
                progress_current=index,
                progress_total=4,
                updated_at=datetime(2026, 8, 19, 3, index, tzinfo=UTC),
            )
        )
    db_session.commit()

    client = TestClient(create_app(settings=settings, acquire_lock=False), base_url=LOOPBACK_ORIGIN)
    AppStatus.should_exit_event = None
    response = client.get("/api/events", headers={"Last-Event-ID": "2"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    assert [event["sequenceId"] for event in events] == [3, 4]
    assert [event["type"] for event in events] == ["job", "job"]


def _parse_sse(body: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    for chunk in normalized.strip().split("\n\n"):
        data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
        events.append(json.loads(data_line.removeprefix("data: ")))
    return events
