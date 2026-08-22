from __future__ import annotations

import pytest
from sqlalchemy import Engine

from app.contracts import JobStatus
from backend.tests.integration.worker_fixture import StudioProcessFixture


@pytest.fixture
def studio_process(migrated_engine: Engine, artifact_store, deterministic_uuid7_factory) -> StudioProcessFixture:
    return StudioProcessFixture(migrated_engine, artifact_store.resolve(), deterministic_uuid7_factory)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["TRANSLATE", "SYNTHESIZE", "MASTER", "EXPORT"])
async def test_restart_resumes_without_duplicate_artifacts(studio_process: StudioProcessFixture, stage: str) -> None:
    run = studio_process.start_fake_chapter(stage=stage)

    await run.run_until_crash(kill_after=f"{stage}:segment:2")
    before = run.ready_artifact_hashes()
    run.kill_worker()
    await run.restart_worker()
    run.wait_success()

    assert before <= run.ready_artifact_hashes()
    assert run.duplicate_ready_cache_keys() == []
    assert run.duplicate_ready_export_manifests() == []
    assert run.provider_calls[f"{stage}:segment:1"] == 1
    assert run.provider_calls[f"{stage}:segment:2"] == 1


@pytest.mark.asyncio
async def test_cancel_preserves_ready_artifacts_and_leaves_no_missing_ready_file(
    studio_process: StudioProcessFixture,
) -> None:
    run = studio_process.start_fake_chapter(stage="SYNTHESIZE")

    await run.run_until_cancel_acknowledged()

    assert run.runner.get(run.job_id).status is JobStatus.CANCELED
    assert run.missing_ready_artifacts() == []
