"""A03 - resumable segment render driven by the real worker, offline.

These tests exercise the SYNTHESIZE handler, the durable per-segment checkpoint and the
synthesis cache with fake adapters only (no network, no downloaded model). They prove
plumbing and resume behaviour: what they cannot prove is Vietnamese voice quality, which
needs the real VieNeu model and is reported NOT_RUN (see
.superpowers/sdd/task-55-report.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ChapterState,
    ImportKind,
    JobKind,
    JobStatus,
    RightsStatus,
    RunStatus,
    SourceType,
)
from app.db.models import (
    Artifact,
    Chapter,
    Project,
    SourceRevision,
    SourceSegment,
    SpeechSegment,
    TranslationRun,
    TranslationSegment,
)
from app.modules.jobs.execution_handlers import build_synthesize_handler
from app.modules.jobs.runner import JobRunner
from app.modules.speech.workflow import SpeechWorkflow
from app.providers.fake import FakeMp3AudioProcessor, FakeTts
from app.worker import Worker
from tests.speech.test_single_narrator import _voice_preset
from tests.voices.test_roles import _extra_preset


PROJECT_ID = "018f0000-0000-7002-8000-000000000001"
CHAPTER_ID = "018f0000-0000-7002-8000-000000000002"
REVISION_ID = "018f0000-0000-7002-8000-000000000003"
RUN_ID = "018f0000-0000-7002-8000-000000000004"


class SimulatedTtsCrash(RuntimeError):
    """Stands in for a worker process dying in the middle of a render."""


class _Fixture:
    def __init__(self, project_id: str, chapter_id: str, revision_id: str, run_id: str) -> None:
        self.project_id = project_id
        self.chapter_id = chapter_id
        self.revision_id = revision_id
        self.run_id = run_id


class CountingFakeTts:
    """FakeTts plus an attempt counter and crash/cancel hooks (still fully offline).

    attempts counts every call the workflow made, including the one that crashed;
    calls records only the syntheses that returned, i.e. the renders that were paid for.
    """

    def __init__(
        self,
        *,
        crash_on_attempt: int | None = None,
        cancel_on_call: int | None = None,
        cancel: object | None = None,
    ) -> None:
        self._adapter = FakeTts()
        self.attempts = 0
        self.calls: list[str] = []
        self.voice_by_segment: dict[str, str] = {}
        self.crash_on_attempt = crash_on_attempt
        self.cancel_on_call = cancel_on_call
        self._cancel = cancel

    def capabilities(self) -> dict[str, object]:
        return self._adapter.capabilities()

    async def list_voices(self, locale: str) -> list[dict[str, object]]:
        return await self._adapter.list_voices(locale)

    async def synthesize(self, request, output_path: Path):
        self.attempts += 1
        if self.crash_on_attempt == self.attempts:
            raise SimulatedTtsCrash(f"simulated crash on attempt {self.attempts}")
        result = await self._adapter.synthesize(request, output_path)
        self.calls.append(request.speech_segment_id)
        self.voice_by_segment[request.speech_segment_id] = request.voice_id
        if self.cancel_on_call == len(self.calls) and self._cancel is not None:
            self._cancel()
        return result


class LyingTts:
    """Writes bytes but reports a different digest: the probe must refuse the artifact."""

    def capabilities(self) -> dict[str, object]:
        return FakeTts().capabilities()

    async def list_voices(self, locale: str) -> list[dict[str, object]]:
        return await FakeTts().list_voices(locale)

    async def synthesize(self, request, output_path: Path):
        from app.contracts import SynthesisResult, Usage, UsageUnit

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"truncated-audio")
        return SynthesisResult(
            provider="fake",
            model="fake-tts",
            provider_version="1",
            duration_ms=1_000,
            sha256="0" * 64,
            usage=(Usage(UsageUnit.AUDIO_SECOND.value, 1),),
        )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _chapter_with_translation(
    db_session,
    *,
    segment_count: int,
    run_status: RunStatus = RunStatus.APPROVED,
) -> _Fixture:
    """Build an approved (or review) chapter whose translation has one segment each."""
    project = Project(
        id=PROJECT_ID,
        title="Resumable",
        slug="resumable-render",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
    )
    chapter = Chapter(
        id=CHAPTER_ID,
        project_id=project.id,
        ordinal=1,
        source_title="Mot",
        state=ChapterState.TRANSLATION_APPROVED.value
        if run_status is RunStatus.APPROVED
        else ChapterState.TRANSLATION_REVIEW.value,
    )
    revision = SourceRevision(
        id=REVISION_ID,
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text="source",
        normalized_sha256=_sha("source"),
        han_char_count=0,
        total_char_count=6,
        normalizer_version="nfc-v1",
    )
    db_session.add(project)
    db_session.flush()
    db_session.add_all((chapter, revision))
    db_session.flush()
    chapter.active_source_revision_id = revision.id
    run = TranslationRun(
        id=RUN_ID,
        chapter_id=chapter.id,
        source_revision_id=revision.id,
        prompt_version="translation-v1",
        status=run_status.value,
        translation_text_sha256="a" * 64,
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    db_session.add(run)
    db_session.flush()
    for index in range(segment_count):
        target_text = f"Cau so {index + 1} giu nhip ke chuyen ro rang va cham rai."
        source = SourceSegment(
            id=f"018f0000-0000-7002-8000-1{index:011x}",
            source_revision_id=revision.id,
            segment_index=index,
            paragraph_start=index,
            paragraph_end=index,
            source_text=f"source {index}",
            source_sha256=_sha(f"source {index}"),
            segment_kind="SOURCE",
        )
        segment = TranslationSegment(
            id=f"018f0000-0000-7002-8000-2{index:011x}",
            translation_run_id=run.id,
            source_segment_id=source.id,
            target_text=target_text,
            target_sha256=_sha(target_text),
            was_cache_hit=False,
            manually_edited=False,
        )
        db_session.add_all((source, segment))
    if run_status is RunStatus.APPROVED:
        chapter.approved_translation_run_id = run.id
    db_session.commit()
    return _Fixture(project.id, chapter.id, revision.id, run.id)


def _configure_plan(db_session, fixture: _Fixture, preset_id: str):
    return SpeechWorkflow(db_session).configure_single(fixture.chapter_id, preset_id)


def _worker_for(engine: Engine, tmp_path: Path, tts) -> tuple[JobRunner, Worker]:
    runner = JobRunner(engine)
    handler = build_synthesize_handler(
        tts=tts,
        audio_processor=FakeMp3AudioProcessor(),
        allow_fake_tts=True,
    )
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    return runner, Worker(
        runner,
        handlers={JobKind.SYNTHESIZE: handler},
        artifact_root=artifact_root,
    )


def _enqueue_synthesize(
    runner: JobRunner,
    fixture: _Fixture,
    idempotency_key: str,
    *,
    voice_plan_id: str | None = None,
    translation_run_id: str | None = None,
):
    return runner.enqueue(
        JobKind.SYNTHESIZE,
        fixture.project_id,
        fixture.chapter_id,
        idempotency_key,
        plan={
            "projectId": fixture.project_id,
            "profileId": "local-tts",
            "cloudConsentId": "",
            "budgetAuthorizationId": "",
            "translationRunId": translation_run_id or fixture.run_id,
            "voicePlanId": voice_plan_id,
        },
    )


def _segment_artifacts(db_session, chapter_id: str) -> list[Artifact]:
    db_session.expire_all()
    return (
        db_session.query(Artifact)
        .filter(
            Artifact.chapter_id == chapter_id,
            Artifact.kind == ArtifactKind.TTS_SEGMENT.value,
        )
        .order_by(Artifact.created_at, Artifact.id)
        .all()
    )


def _ready_segment_artifacts(db_session, chapter_id: str) -> list[Artifact]:
    return [
        artifact
        for artifact in _segment_artifacts(db_session, chapter_id)
        if artifact.status == ArtifactStatus.READY.value
    ]


def _work_leftovers(artifact_root: Path) -> list[Path]:
    """Staging files the render must have cleaned up (no half written audio)."""
    return sorted(
        path
        for path in artifact_root.rglob("*")
        if path.is_file()
        and (path.name.endswith(".partial") or path.name.startswith(".tmp-"))
    )


@pytest.fixture
def worker_db_path(migrated_engine: Engine) -> Path:
    return Path(migrated_engine.url.database)


@pytest.mark.asyncio
async def test_synthesize_job_without_plan_fails_closed(
    worker_db_path: Path,
    migrated_engine: Engine,
    db_session,
    tmp_path: Path,
) -> None:
    fixture = _chapter_with_translation(db_session, segment_count=3)
    preset = _voice_preset(db_session)
    _configure_plan(db_session, fixture, preset.id)
    tts = CountingFakeTts()
    runner, worker = _worker_for(migrated_engine, tmp_path, tts)

    job = runner.enqueue(
        JobKind.SYNTHESIZE, fixture.project_id, fixture.chapter_id, "no-plan"
    )
    await worker.run_once()

    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.error_code == "SYNTHESIZE_PLAN_REQUIRED"
    assert tts.attempts == 0
    assert _segment_artifacts(db_session, fixture.chapter_id) == []


@pytest.mark.asyncio
async def test_synthesize_job_without_approved_translation_renders_no_segment(
    worker_db_path: Path,
    migrated_engine: Engine,
    db_session,
    tmp_path: Path,
) -> None:
    fixture = _chapter_with_translation(db_session, segment_count=3)
    preset = _voice_preset(db_session)
    plan = _configure_plan(db_session, fixture, preset.id)
    chapter = db_session.get(Chapter, fixture.chapter_id)
    chapter.approved_translation_run_id = None
    db_session.commit()
    tts = CountingFakeTts()
    runner, worker = _worker_for(migrated_engine, tmp_path, tts)

    job = _enqueue_synthesize(
        runner, fixture, "no-approval", voice_plan_id=plan.id
    )
    await worker.run_once()

    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.error_code == "SYNTHESIZE_TRANSLATION_APPROVAL_REQUIRED"
    assert tts.attempts == 0
    assert tts.calls == []
    assert _segment_artifacts(db_session, fixture.chapter_id) == []


@pytest.mark.asyncio
async def test_synthesize_job_rejects_run_that_is_no_longer_approved(
    worker_db_path: Path,
    migrated_engine: Engine,
    db_session,
    tmp_path: Path,
) -> None:
    fixture = _chapter_with_translation(db_session, segment_count=3)
    preset = _voice_preset(db_session)
    plan = _configure_plan(db_session, fixture, preset.id)
    run = db_session.get(TranslationRun, fixture.run_id)
    run.status = RunStatus.REVIEW.value
    db_session.commit()
    tts = CountingFakeTts()
    runner, worker = _worker_for(migrated_engine, tmp_path, tts)

    job = _enqueue_synthesize(runner, fixture, "run-revoked", voice_plan_id=plan.id)
    await worker.run_once()

    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.error_code == "SYNTHESIZE_TRANSLATION_APPROVAL_REQUIRED"
    assert tts.attempts == 0
    assert _segment_artifacts(db_session, fixture.chapter_id) == []


@pytest.mark.asyncio
async def test_synthesize_job_rejects_stale_voice_plan(
    worker_db_path: Path,
    migrated_engine: Engine,
    db_session,
    tmp_path: Path,
) -> None:
    fixture = _chapter_with_translation(db_session, segment_count=3)
    preset = _voice_preset(db_session)
    _configure_plan(db_session, fixture, preset.id)
    tts = CountingFakeTts()
    runner, worker = _worker_for(migrated_engine, tmp_path, tts)

    job = _enqueue_synthesize(
        runner, fixture, "stale-plan", voice_plan_id="0" * 36
    )
    await worker.run_once()

    view = runner.get(job.id)
    assert view.status is JobStatus.FAILED
    assert view.error_code == "SYNTHESIZE_VOICE_PLAN_STALE"
    assert tts.attempts == 0


@pytest.mark.asyncio
async def test_crash_then_resume_renders_every_segment_exactly_once(
    worker_db_path: Path,
    migrated_engine: Engine,
    db_session,
    tmp_path: Path,
) -> None:
    segment_count = 60
    committed_before_crash = 20
    fixture = _chapter_with_translation(db_session, segment_count=segment_count)
    preset = _voice_preset(db_session)
    plan = _configure_plan(db_session, fixture, preset.id)
    tts = CountingFakeTts(crash_on_attempt=committed_before_crash + 1)
    runner, worker = _worker_for(migrated_engine, tmp_path, tts)

    first = _enqueue_synthesize(runner, fixture, "crash-1", voice_plan_id=plan.id)
    await worker.run_once()

    assert runner.get(first.id).status is JobStatus.FAILED
    assert tts.calls and len(tts.calls) == committed_before_crash
    assert len(_ready_segment_artifacts(db_session, fixture.chapter_id)) == committed_before_crash
    assert _work_leftovers(tmp_path / "artifacts") == []

    second = _enqueue_synthesize(runner, fixture, "crash-2", voice_plan_id=plan.id)
    await worker.run_once()

    assert runner.get(second.id).status is JobStatus.SUCCEEDED
    ready = _ready_segment_artifacts(db_session, fixture.chapter_id)
    assert len(ready) == segment_count
    assert len({artifact.id for artifact in ready}) == segment_count
    assert len(tts.calls) == segment_count
    assert tts.attempts == segment_count + 1
    statuses = {artifact.status for artifact in _segment_artifacts(db_session, fixture.chapter_id)}
    assert statuses == {ArtifactStatus.READY.value}
    assert _work_leftovers(tmp_path / "artifacts") == []


@pytest.mark.asyncio
async def test_second_pass_over_a_finished_chapter_renders_nothing(
    worker_db_path: Path,
    migrated_engine: Engine,
    db_session,
    tmp_path: Path,
) -> None:
    fixture = _chapter_with_translation(db_session, segment_count=5)
    preset = _voice_preset(db_session)
    plan = _configure_plan(db_session, fixture, preset.id)
    tts = CountingFakeTts()
    runner, worker = _worker_for(migrated_engine, tmp_path, tts)

    first = _enqueue_synthesize(runner, fixture, "idem-1", voice_plan_id=plan.id)
    await worker.run_once()
    second = _enqueue_synthesize(runner, fixture, "idem-2", voice_plan_id=plan.id)
    await worker.run_once()

    assert runner.get(first.id).status is JobStatus.SUCCEEDED
    assert runner.get(second.id).status is JobStatus.SUCCEEDED
    assert len(tts.calls) == 5
    assert len(_ready_segment_artifacts(db_session, fixture.chapter_id)) == 5


@pytest.mark.asyncio
async def test_cancel_stops_at_segment_boundary_and_keeps_ready_segments(
    worker_db_path: Path,
    migrated_engine: Engine,
    db_session,
    tmp_path: Path,
) -> None:
    segment_count = 12
    cancel_after = 5
    fixture = _chapter_with_translation(db_session, segment_count=segment_count)
    preset = _voice_preset(db_session)
    plan = _configure_plan(db_session, fixture, preset.id)
    runner_holder: dict[str, JobRunner] = {}
    job_holder: dict[str, str] = {}

    def request_cancel() -> None:
        runner_holder["runner"].request_cancel(
            job_holder["job_id"], datetime.now(UTC)
        )

    tts = CountingFakeTts(cancel_on_call=cancel_after, cancel=request_cancel)
    runner, worker = _worker_for(migrated_engine, tmp_path, tts)
    runner_holder["runner"] = runner

    job = _enqueue_synthesize(runner, fixture, "cancel-1", voice_plan_id=plan.id)
    job_holder["job_id"] = job.id
    await worker.run_once()

    assert runner.get(job.id).status is JobStatus.CANCELED
    assert len(tts.calls) == cancel_after
    ready = _ready_segment_artifacts(db_session, fixture.chapter_id)
    assert len(ready) == cancel_after
    assert _work_leftovers(tmp_path / "artifacts") == []

    resumed = _enqueue_synthesize(runner, fixture, "cancel-2", voice_plan_id=plan.id)
    await worker.run_once()

    assert runner.get(resumed.id).status is JobStatus.SUCCEEDED
    assert len(_ready_segment_artifacts(db_session, fixture.chapter_id)) == segment_count
    assert len(tts.calls) == segment_count


def test_probe_rejects_wrong_digest_and_leaves_no_artifact(
    db_session, tmp_path: Path
) -> None:
    fixture = _chapter_with_translation(db_session, segment_count=2)
    preset = _voice_preset(db_session)
    plan = _configure_plan(db_session, fixture, preset.id)
    workflow = SpeechWorkflow(
        db_session,
        tts=LyingTts(),
        audio_processor=FakeMp3AudioProcessor(),
        artifact_root=tmp_path / "artifacts",
    )

    with pytest.raises(ValueError, match="TTS_CHECKSUM_MISMATCH"):
        workflow.synthesize_segments(fixture.chapter_id)

    assert _segment_artifacts(db_session, fixture.chapter_id) == []
    assert _work_leftovers(tmp_path / "artifacts") == []
    assert plan.id == db_session.get(Chapter, fixture.chapter_id).active_voice_plan_id


def test_text_change_invalidates_only_that_segment(db_session, tmp_path: Path) -> None:
    fixture = _chapter_with_translation(db_session, segment_count=3)
    preset = _voice_preset(db_session)
    _configure_plan(db_session, fixture, preset.id)
    tts = CountingFakeTts()
    workflow = SpeechWorkflow(
        db_session,
        tts=tts,
        audio_processor=FakeMp3AudioProcessor(),
        artifact_root=tmp_path / "artifacts",
    )

    first = workflow.synthesize_segments(fixture.chapter_id)
    assert set(first.rendered_segment_ids) == set(first.segment_ids)
    assert first.reused_segment_ids == ()
    assert len(tts.calls) == 3
    first_ready = {artifact.id for artifact in _ready_segment_artifacts(db_session, fixture.chapter_id)}
    assert len(first_ready) == 3

    edited = db_session.get(SpeechSegment, first.segment_ids[1])
    edited.narration_text = "Cau so hai da duoc sua lai cho dung ngu canh."
    edited.narration_sha256 = _sha(edited.narration_text)
    db_session.commit()

    calls_before = len(tts.calls)
    second = workflow.synthesize_segments(fixture.chapter_id)

    assert second.rendered_segment_ids == (first.segment_ids[1],)
    assert set(second.reused_segment_ids) == {
        first.segment_ids[0],
        first.segment_ids[2],
    }
    assert len(tts.calls) - calls_before == 1
    assert tts.calls[-1] == first.segment_ids[1]

    ready_ids = {artifact.id for artifact in _ready_segment_artifacts(db_session, fixture.chapter_id)}
    assert len(ready_ids) == 3
    stale = [
        artifact
        for artifact in _segment_artifacts(db_session, fixture.chapter_id)
        if artifact.status == ArtifactStatus.SUPERSEDED.value
    ]
    assert len(stale) == 1
    assert stale[0].metadata_json["speech_segment_id"] == first.segment_ids[1]


def test_changing_the_narrator_preset_rerenders_every_segment(
    db_session, tmp_path: Path
) -> None:
    from app.modules.voices.roles import AssistedVoicePlanService

    fixture = _chapter_with_translation(db_session, segment_count=3)
    preset = _voice_preset(db_session)
    replacement = _extra_preset(db_session, 5)
    plan = _configure_plan(db_session, fixture, preset.id)
    tts = CountingFakeTts()
    workflow = SpeechWorkflow(
        db_session,
        tts=tts,
        audio_processor=FakeMp3AudioProcessor(),
        artifact_root=tmp_path / "artifacts",
    )

    first = workflow.synthesize_segments(fixture.chapter_id)
    assert len(tts.calls) == 3
    assert set(tts.voice_by_segment.values()) == {preset.provider_voice_id}

    revised = AssistedVoicePlanService(db_session).change_role_voice(
        plan.id, "narrator", replacement.id
    )
    calls_before = len(tts.calls)
    second = workflow.synthesize_segments(fixture.chapter_id)

    assert second.reused_segment_ids == ()
    assert set(second.rendered_segment_ids) == set(second.segment_ids)
    assert len(second.segment_ids) == 3
    assert set(second.segment_ids) != set(first.segment_ids)
    assert len(tts.calls) - calls_before == 3
    assert set(tts.voice_by_segment[segment_id] for segment_id in second.segment_ids) == {
        replacement.provider_voice_id
    }
    assert len(_ready_segment_artifacts(db_session, fixture.chapter_id)) == 3
    assert (
        revised.id
        == db_session.get(Chapter, fixture.chapter_id).active_voice_plan_id
    )


@pytest.mark.asyncio
async def test_worker_render_maps_each_segment_to_its_role_voice(
    worker_db_path: Path,
    migrated_engine: Engine,
    db_session,
    tmp_path: Path,
) -> None:
    from app.modules.voices.roles import AssistedVoicePlanService

    fixture = _chapter_with_translation(db_session, segment_count=3)
    narrator = _voice_preset(db_session)
    hero = _extra_preset(db_session, 6)
    roles = AssistedVoicePlanService(db_session)
    base = roles.create_multi(
        fixture.chapter_id,
        narrator_preset_id=narrator.id,
        roles=(("hero", "Hero", hero.id),),
    )
    assigned = roles.assign_roles(
        base.id,
        expected_hash=base.plan_sha256,
        assignments={base.segments[0].id: "hero"},
    )
    tts = CountingFakeTts()
    runner, worker = _worker_for(migrated_engine, tmp_path, tts)

    job = runner.enqueue(
        JobKind.SYNTHESIZE,
        fixture.project_id,
        fixture.chapter_id,
        "roles-1",
        plan={
            "projectId": fixture.project_id,
            "profileId": "local-tts",
            "cloudConsentId": "",
            "budgetAuthorizationId": "",
            "translationRunId": fixture.run_id,
            "voicePlanId": assigned.id,
        },
    )
    await worker.run_once()

    assert runner.get(job.id).status is JobStatus.SUCCEEDED
    hero_segment_id, *narrator_segment_ids = [segment.id for segment in assigned.segments]
    assert tts.voice_by_segment[hero_segment_id] == hero.provider_voice_id
    assert {tts.voice_by_segment[segment_id] for segment_id in narrator_segment_ids} == {
        narrator.provider_voice_id
    }
    assert len(tts.calls) == 3


@pytest.mark.slow
@pytest.mark.asyncio
async def test_five_hundred_segment_chapter_survives_a_crash_without_double_ready(
    worker_db_path: Path,
    migrated_engine: Engine,
    db_session,
    tmp_path: Path,
) -> None:
    """A03 acceptance fixture: 500 segments, real worker, crash at 120, resume."""
    import time

    segment_count = 500
    committed_before_crash = 120
    fixture = _chapter_with_translation(db_session, segment_count=segment_count)
    preset = _voice_preset(db_session)
    plan = _configure_plan(db_session, fixture, preset.id)
    tts = CountingFakeTts(crash_on_attempt=committed_before_crash + 1)
    runner, worker = _worker_for(migrated_engine, tmp_path, tts)

    started = time.perf_counter()
    first = _enqueue_synthesize(runner, fixture, "bulk-1", voice_plan_id=plan.id)
    await worker.run_once()
    crashed = time.perf_counter()

    assert runner.get(first.id).status is JobStatus.FAILED
    assert len(tts.calls) == committed_before_crash
    assert (
        len(_ready_segment_artifacts(db_session, fixture.chapter_id))
        == committed_before_crash
    )

    second = _enqueue_synthesize(runner, fixture, "bulk-2", voice_plan_id=plan.id)
    await worker.run_once()
    finished = time.perf_counter()

    assert runner.get(second.id).status is JobStatus.SUCCEEDED
    ready = _ready_segment_artifacts(db_session, fixture.chapter_id)
    assert len(ready) == segment_count
    assert len({artifact.id for artifact in ready}) == segment_count
    assert len(tts.calls) == segment_count
    assert tts.attempts == segment_count + 1
    assert {
        artifact.status for artifact in _segment_artifacts(db_session, fixture.chapter_id)
    } == {ArtifactStatus.READY.value}
    assert _work_leftovers(tmp_path / "artifacts") == []
    print(
        f"[A03] 500-segment worker render: crash-after-{committed_before_crash} "
        f"took {crashed - started:.1f}s, resume took {finished - crashed:.1f}s, "
        f"adapter calls {len(tts.calls)}"
    )
