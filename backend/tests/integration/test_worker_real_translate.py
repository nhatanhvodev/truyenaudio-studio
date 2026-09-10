"""J01: crash coverage for the **real** TRANSLATE handler (round 4).

The existing `worker_fixture` crash harness drives a synthetic stage handler.
This module drives the production handler (`build_translate_handler`) through the
same process-crash protocol, using the local fake translator (no cloud call):

1. the worker dies **before** the provider result is returned -> nothing is
   committed, recovery re-runs the attempt and the chapter still gets exactly
   one segment per source segment;
2. the worker dies **after** the workflow committed the run -> recovery re-runs
   the handler, and the second run is served entirely from the local cache, so
   the provider is **not** called (and not billed) again.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from app.api.translation import CleanFakeTranslator
from app.contracts import (
    ChapterState,
    ImportKind,
    JobKind,
    JobStatus,
    ProviderKind,
    RightsStatus,
    RunStatus,
    SourceType,
)
from app.db.base import session_factory
from app.db.models import (
    Chapter,
    ProviderProfile,
    Project,
    SourceRevision,
    SourceSegment,
    TranslationRun,
    TranslationSegment,
)
from app.modules.jobs.execution_handlers import build_translate_handler
from app.modules.jobs.runner import JobRunner
from app.modules.translation import workflow as workflow_module
from app.settings.config import Settings
from app.worker import Worker
from backend.tests.integration.worker_fixture import ManualClock, SimulatedWorkerCrash

NOW = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)
PROJECT_ID = "018f0000-0000-7000-8000-930000000001"
CHAPTER_ID = "018f0000-0000-7000-8000-930000000002"
REVISION_ID = "018f0000-0000-7000-8000-930000000003"
PROFILE_ID = "018f0000-0000-7000-8000-930000000004"
SEGMENT_IDS = (
    "018f0000-0000-7000-8000-930000000011",
    "018f0000-0000-7000-8000-930000000012",
)
PLAN = {
    "projectId": PROJECT_ID,
    "profileId": PROFILE_ID,
    "cloudConsentId": "consent-1",
    "budgetAuthorizationId": "budget-1",
    "revisionId": REVISION_ID,
}


@dataclass
class ProviderCalls:
    """Counts adapter invocations so a re-billed provider becomes visible."""

    success: int = 0
    failures: int = 0
    by_segment: dict[str, int] = field(default_factory=dict)

    def install(self, monkeypatch, *, fail_first: bool = False) -> None:
        original = CleanFakeTranslator.translate
        state = {"fail_first": fail_first, "armed": fail_first}
        calls = self

        async def counting(translator: CleanFakeTranslator, request):
            if state["armed"]:
                state["armed"] = False
                calls.failures += 1
                raise SimulatedWorkerCrash("provider-before-result")
            result = await original(translator, request)
            calls.success += 1
            calls.by_segment[request.source_segment_id] = (
                calls.by_segment.get(request.source_segment_id, 0) + 1
            )
            return result

        monkeypatch.setattr(CleanFakeTranslator, "translate", counting)


@dataclass
class RealTranslateRun:
    engine: Engine
    db_path: Path
    settings: Settings
    artifact_root: Path
    runner: JobRunner
    clock: ManualClock
    job_id: str
    calls: ProviderCalls

    def worker(self) -> Worker:
        return Worker(
            self.runner,
            handlers={
                JobKind.TRANSLATE: build_translate_handler(self.settings, db_path=self.db_path)
            },
            worker_id="worker-a",
            clock=self.clock,
            heartbeat_interval_seconds=0.01,
            artifact_root=self.artifact_root,
        )

    def crash_worker(self) -> None:
        """Lease expiry: the harness 'kills' the process and a new one starts."""
        self.clock.value = NOW + timedelta(seconds=203)

    async def restart_worker(self) -> None:
        worker = self.worker()
        assert await worker.run_once() is False  # recovery only: no claimable job yet
        next_run_at = self.runner.get(self.job_id).next_run_at
        if next_run_at is not None:
            self.clock.value = next_run_at
        assert await worker.run_once() is True

    def runs(self) -> list[TranslationRun]:
        with session_factory(self.engine)() as session:
            return list(
                session.scalars(
                    select(TranslationRun)
                    .where(TranslationRun.chapter_id == CHAPTER_ID)
                    .order_by(TranslationRun.created_at, TranslationRun.id)
                ).all()
            )

    def segments_of(self, run_id: str) -> list[TranslationSegment]:
        with session_factory(self.engine)() as session:
            return list(
                session.scalars(
                    select(TranslationSegment)
                    .where(TranslationSegment.translation_run_id == run_id)
                    .order_by(TranslationSegment.source_segment_id)
                ).all()
            )

    def chapter_state(self) -> str:
        with session_factory(self.engine)() as session:
            return session.get(Chapter, CHAPTER_ID).state


@pytest.fixture
def real_translate(migrated_engine: Engine, artifact_store, deterministic_uuid7_factory) -> RealTranslateRun:
    db_path = Path(migrated_engine.url.database)
    _seed(migrated_engine)
    runner = JobRunner(migrated_engine, id_factory=deterministic_uuid7_factory)
    job = runner.enqueue(
        JobKind.TRANSLATE,
        PROJECT_ID,
        CHAPTER_ID,
        "real-translate",
        plan=PLAN,
    )
    return RealTranslateRun(
        engine=migrated_engine,
        db_path=db_path,
        settings=Settings(data_root=db_path.parent),
        artifact_root=artifact_store.resolve(),
        runner=runner,
        clock=ManualClock(NOW),
        job_id=job.id,
        calls=ProviderCalls(),
    )


def test_real_handler_crash_before_provider_result_recovers_without_duplicates(
    real_translate: RealTranslateRun, monkeypatch
) -> None:
    real_translate.calls.install(monkeypatch, fail_first=True)

    with pytest.raises(SimulatedWorkerCrash):
        asyncio.run(real_translate.worker().run_once())

    # Nothing was committed by the dead process: the job is still leased and the
    # chapter has no run yet.
    assert real_translate.runner.get(real_translate.job_id).status is JobStatus.RUNNING
    assert real_translate.runs() == []
    assert real_translate.calls.failures == 1

    real_translate.crash_worker()
    asyncio.run(real_translate.restart_worker())

    assert real_translate.runner.get(real_translate.job_id).status is JobStatus.SUCCEEDED
    runs = real_translate.runs()
    assert len(runs) == 1
    assert runs[0].status == RunStatus.REVIEW.value
    segments = real_translate.segments_of(runs[0].id)
    assert [segment.source_segment_id for segment in segments] == list(SEGMENT_IDS)
    assert all(segment.was_cache_hit is False for segment in segments)
    # Exactly one successful provider call per source segment: the crashed
    # attempt is not retried on top of the recovery attempt.
    assert real_translate.calls.by_segment == {SEGMENT_IDS[0]: 1, SEGMENT_IDS[1]: 1}
    assert real_translate.chapter_state() == ChapterState.TRANSLATION_REVIEW.value


def test_real_handler_crash_after_commit_does_not_call_provider_again(
    real_translate: RealTranslateRun, monkeypatch
) -> None:
    real_translate.calls.install(monkeypatch)

    original_enqueue = workflow_module.TranslationWorkflow.enqueue_translation
    state = {"armed": True}

    def crash_after_commit(self, chapter_id, **kwargs):
        view = original_enqueue(self, chapter_id, **kwargs)
        if state["armed"]:
            state["armed"] = False
            raise SimulatedWorkerCrash("after-commit")
        return view

    monkeypatch.setattr(
        workflow_module.TranslationWorkflow, "enqueue_translation", crash_after_commit
    )

    with pytest.raises(SimulatedWorkerCrash):
        asyncio.run(real_translate.worker().run_once())

    # The run was committed before the crash, but the job never completed.
    committed = real_translate.runs()
    assert len(committed) == 1
    assert len(real_translate.segments_of(committed[0].id)) == 2
    assert real_translate.runner.get(real_translate.job_id).status is JobStatus.RUNNING
    assert real_translate.calls.by_segment == {SEGMENT_IDS[0]: 1, SEGMENT_IDS[1]: 1}

    real_translate.crash_worker()
    asyncio.run(real_translate.restart_worker())

    assert real_translate.runner.get(real_translate.job_id).status is JobStatus.SUCCEEDED
    runs = real_translate.runs()
    assert len(runs) == 2
    replayed = real_translate.segments_of(runs[1].id)
    assert len(replayed) == 2
    # The recovery attempt is served from the local translation cache: the
    # provider is not called (and not billed) a second time.
    assert all(segment.was_cache_hit is True for segment in replayed)
    assert real_translate.calls.by_segment == {SEGMENT_IDS[0]: 1, SEGMENT_IDS[1]: 1}
    assert real_translate.calls.success == 2


def _seed(engine: Engine) -> None:
    session = session_factory(engine)()
    try:
        session.add(
            Project(
                id=PROJECT_ID,
                title="Real Translate",
                slug="real-translate",
                source_type=SourceType.SELF_AUTHORED.value,
                rights_status=RightsStatus.PRIVATE_ONLY.value,
                default_language="zh-CN",
                target_language="vi-VN",
            )
        )
        session.flush()
        chapter = Chapter(
            id=CHAPTER_ID,
            project_id=PROJECT_ID,
            ordinal=1,
            state=ChapterState.NORMALIZED.value,
        )
        session.add(chapter)
        session.flush()
        revision = SourceRevision(
            id=REVISION_ID,
            chapter_id=CHAPTER_ID,
            revision_no=1,
            import_kind=ImportKind.PASTE.value,
            normalized_text="一\n二",
            normalized_sha256=_sha("一\n二"),
            han_char_count=2,
            total_char_count=3,
            normalizer_version="nfc-v1",
        )
        session.add(revision)
        session.flush()
        chapter.active_source_revision_id = revision.id
        session.add(
            ProviderProfile(
                id=PROFILE_ID,
                provider_kind=ProviderKind.TRANSLATOR.value,
                adapter_name="fake-hanviet",
                display_name="Fake Han-Viet",
                model="fake-hanviet-v2",
                enabled=True,
                revision=1,
            )
        )
        for index, text in enumerate(("一", "二")):
            session.add(
                SourceSegment(
                    id=SEGMENT_IDS[index],
                    source_revision_id=REVISION_ID,
                    segment_index=index,
                    paragraph_start=index,
                    paragraph_end=index,
                    source_text=text,
                    source_sha256=_sha(text),
                    segment_kind="SOURCE",
                )
            )
        session.commit()
    finally:
        session.close()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
