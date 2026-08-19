from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.contracts import JobKind, RightsStatus, SourceType
from app.modules.jobs.runner import JobLease, JobRunner
from app.worker import Worker


NOW = datetime(2026, 8, 19, 6, 0, 0, tzinfo=UTC)
PROJECT_ID = "018f0000-0000-7000-8000-000000008001"
CHAPTER_ID = "018f0000-0000-7000-8000-000000008002"


def _insert_project(runner: JobRunner) -> None:
    from sqlalchemy import text

    with runner.engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects (id, title, slug, source_type, rights_status, created_at, updated_at)
                VALUES (:id, 'Heartbeat', 'heartbeat', :source_type, :rights_status, :now, :now)
                """
            ),
            {
                "id": PROJECT_ID,
                "source_type": SourceType.SELF_AUTHORED.value,
                "rights_status": RightsStatus.PRIVATE_ONLY.value,
                "now": NOW.isoformat(),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO chapters (id, project_id, ordinal, state, created_at, updated_at)
                VALUES (:id, :project_id, 1, 'IMPORTED', :now, :now)
                """
            ),
            {"id": CHAPTER_ID, "project_id": PROJECT_ID, "now": NOW.isoformat()},
        )


@pytest.mark.asyncio
async def test_worker_refreshes_atomic_local_heartbeat_when_idle(migrated_engine, tmp_path: Path) -> None:
    worker = Worker(
        JobRunner(migrated_engine),
        handlers={},
        worker_id="worker-heartbeat",
        clock=lambda: NOW,
        process_heartbeat_path=tmp_path / "worker-heartbeat.json",
    )

    assert await worker.run_once() is False

    heartbeat = json.loads((tmp_path / "worker-heartbeat.json").read_text(encoding="utf-8"))
    assert heartbeat["status"] == "idle"
    assert heartbeat["updated_at"] == NOW.isoformat()
    assert list(tmp_path.glob("*.partial")) == []


@pytest.mark.asyncio
async def test_worker_refreshes_atomic_local_heartbeat_while_working(migrated_engine, tmp_path: Path) -> None:
    runner = JobRunner(migrated_engine)
    _insert_project(runner)
    runner.enqueue(JobKind.TRANSLATE, PROJECT_ID, CHAPTER_ID, "heartbeat-working")

    async def handle(lease: JobLease) -> None:
        heartbeat = json.loads((tmp_path / "worker-heartbeat.json").read_text(encoding="utf-8"))
        assert heartbeat["status"] == "working"
        assert heartbeat["job_kind"] == "TRANSLATE"
        return None

    worker = Worker(
        runner,
        handlers={JobKind.TRANSLATE: handle},
        worker_id="worker-heartbeat",
        clock=lambda: NOW,
        process_heartbeat_path=tmp_path / "worker-heartbeat.json",
    )

    assert await worker.run_once() is True
    heartbeat = json.loads((tmp_path / "worker-heartbeat.json").read_text(encoding="utf-8"))
    assert heartbeat["status"] == "idle"
