from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sse_starlette.sse import AppStatus

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text

from app.contracts import JobKind, JobStatus, RightsStatus, SourceType
from app.db.base import session_factory
from app.db.models import EventLog, Job, Project
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


def test_events_resume_uses_durable_sequence_after_job_updated_at_reorders(
    settings: Settings,
    db_session,
) -> None:
    project = Project(
        id="018f0000-0000-7000-8000-000000007201",
        title="Durable SSE",
        slug="durable-sse",
        source_type=SourceType.SELF_AUTHORED.value,
        rights_status=RightsStatus.CLEARED.value,
    )
    first = Job(
        id="018f0000-0000-7000-8000-000000007211",
        kind=JobKind.TRANSLATE.value,
        status=JobStatus.QUEUED.value,
        project_id=project.id,
        idempotency_key="durable-first",
        created_at=datetime(2026, 8, 19, 3, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 19, 3, 0, tzinfo=UTC),
    )
    second = Job(
        id="018f0000-0000-7000-8000-000000007212",
        kind=JobKind.SYNTHESIZE.value,
        status=JobStatus.RUNNING.value,
        project_id=project.id,
        idempotency_key="durable-second",
        created_at=datetime(2026, 8, 19, 3, 1, tzinfo=UTC),
        updated_at=datetime(2026, 8, 19, 3, 1, tzinfo=UTC),
    )
    db_session.add(project)
    db_session.flush()
    db_session.add_all([first, second])
    db_session.commit()

    client = TestClient(create_app(settings=settings, acquire_lock=False), base_url=LOOPBACK_ORIGIN)
    AppStatus.should_exit_event = None
    initial_events = _parse_sse(client.get("/api/events").text)
    first_sequence = initial_events[0]["sequenceId"]

    first.updated_at = datetime(2026, 8, 19, 5, 0, tzinfo=UTC)
    db_session.commit()
    AppStatus.should_exit_event = None
    replay = _parse_sse(client.get("/api/events", headers={"Last-Event-ID": str(first_sequence)}).text)

    assert [event["jobId"] for event in initial_events] == [first.id, second.id]
    assert [event["sequenceId"] for event in initial_events] == [1, 2]
    assert [event["jobId"] for event in replay] == [second.id]
    assert [event["sequenceId"] for event in replay] == [2]


def test_events_include_audit_and_usage_without_secret_details(settings: Settings, db_session) -> None:
    project = Project(
        id="018f0000-0000-7000-8000-000000007301",
        title="Mixed SSE",
        slug="mixed-sse",
        source_type=SourceType.SELF_AUTHORED.value,
        rights_status=RightsStatus.CLEARED.value,
    )
    job = Job(
        id="018f0000-0000-7000-8000-000000007311",
        kind=JobKind.EXPORT.value,
        status=JobStatus.SUCCEEDED.value,
        project_id=project.id,
        idempotency_key="mixed-job",
        created_at=datetime(2026, 8, 19, 4, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 19, 4, 0, tzinfo=UTC),
    )
    db_session.add(project)
    db_session.flush()
    db_session.add(job)
    db_session.flush()
    db_session.execute(
        text(
            """
            INSERT INTO audit_events
            (id, actor, action, entity_type, entity_id, redacted_details, created_at)
            VALUES
            ('018f0000-0000-7000-8000-000000007321', 'LOCAL_OWNER', 'EXPORT_REVIEWED',
             'project', :project_id, :details, '2026-08-19T04:01:00+00:00')
            """
        ),
        {"project_id": project.id, "details": '{"secret":"sk-test-1234567890abcdef","source":"秘密全文"}'},
    )
    db_session.execute(
        text(
            """
            INSERT INTO usage_ledger
            (id, provider, model, operation_id, unit, measured_units,
             actual_vnd, billing_confidence, created_at)
            VALUES
            ('018f0000-0000-7000-8000-000000007331', 'fake', 'fake-local',
             'op-mixed', 'CHARACTER', 123, 0, 'CONFIRMED', '2026-08-19T04:02:00+00:00')
            """
        )
    )
    db_session.commit()

    client = TestClient(create_app(settings=settings, acquire_lock=False), base_url=LOOPBACK_ORIGIN)
    AppStatus.should_exit_event = None
    response = client.get("/api/events")
    events = _parse_sse(response.text)

    assert [event["type"] for event in events] == ["job", "audit", "usage"]
    assert [event["sequenceId"] for event in events] == [1, 2, 3]
    assert events[1] == {
        "sequenceId": 2,
        "type": "audit",
        "auditId": "018f0000-0000-7000-8000-000000007321",
        "actor": "LOCAL_OWNER",
        "action": "EXPORT_REVIEWED",
        "entityType": "project",
        "entityId": project.id,
    }
    assert events[2]["provider"] == "fake"
    assert events[2]["measuredUnits"] == 123
    assert "sk-test-1234567890abcdef" not in response.text
    assert "秘密全文" not in response.text


def test_event_log_backfill_ignores_row_inserted_by_concurrent_stream(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.events import _sync_event_log

    project = Project(
        id="018f0000-0000-7000-8000-000000007401",
        title="Concurrent SSE",
        slug="concurrent-sse",
        source_type=SourceType.SELF_AUTHORED.value,
        rights_status=RightsStatus.CLEARED.value,
    )
    job = Job(
        id="018f0000-0000-7000-8000-000000007411",
        kind=JobKind.EXPORT.value,
        status=JobStatus.QUEUED.value,
        project_id=project.id,
        idempotency_key="concurrent-job",
        created_at=datetime(2026, 8, 19, 6, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 19, 6, 0, tzinfo=UTC),
    )
    db_session.add(project)
    db_session.flush()
    db_session.add(job)
    db_session.commit()
    original_add = db_session.add
    raced = False

    def add_after_concurrent_insert(instance: object, *args: object, **kwargs: object) -> None:
        nonlocal raced
        if isinstance(instance, EventLog) and not raced:
            raced = True
            factory = session_factory(db_session.get_bind())
            with factory() as concurrent:
                concurrent.add(
                    EventLog(
                        entity_type=instance.entity_type,
                        entity_id=instance.entity_id,
                        created_at=instance.created_at,
                    )
                )
                concurrent.commit()
        original_add(instance, *args, **kwargs)

    monkeypatch.setattr(db_session, "add", add_after_concurrent_insert)

    try:
        _sync_event_log(db_session)
    except IntegrityError as exc:
        raise AssertionError("event backfill should ignore rows inserted by a concurrent stream") from exc

    assert raced
    assert db_session.query(EventLog).filter_by(entity_type="job", entity_id=job.id).count() == 1


def _parse_sse(body: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    for chunk in normalized.strip().split("\n\n"):
        data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
        events.append(json.loads(data_line.removeprefix("data: ")))
    return events
