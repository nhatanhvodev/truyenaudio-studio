"""Worker execution handlers wired to real domain services (task J01, extended by A03).

TRANSLATE handler executes the existing guarded translation workflow under
the worker. Cloud/model providers (qwen/gemini) reuse the API workflow
scopes (guarded adapter + fresh session); the local fake provider runs the
deterministic hanviet translator. Plan and revision are validated before any
dispatch; a rejection surfaces as a HandlerUnavailable subclass so the worker
fails the job with a precise, non-retryable code.

SYNTHESIZE handler renders the segment audio of the chapter's active voice plan
under the worker, checkpointing every finished segment on the recovery session
so an interrupted attempt resumes from the synthesis cache instead of paying
for the same segment twice. It runs on the recovery session (not a private
engine) precisely because that session is the one the checkpoints commit; the
worker owns both the database and the artifact root.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping

from app.contracts import AudioProcessor, JobKind, TtsAdapter
from app.db.base import create_engine_for, session_factory
from app.db.models import Chapter, ProviderProfile
from app.modules.jobs.handlers import PlanRequiredError, require_plan
from app.modules.jobs.recovery import RecoveryJobContext
from app.modules.speech.workflow import (
    SpeechWorkflow,
    TranslationApprovalRequired,
    TtsUnavailable,
    VoicePlanRequired,
)
from app.settings.config import Settings
from app.worker import HandlerUnavailable


class TranslateReject(HandlerUnavailable):
    def __init__(self, code: str) -> None:
        super().__init__(JobKind.TRANSLATE)
        self.code = code


def build_translate_handler(settings: Settings, *, db_path: object | None = None) -> Callable[..., Awaitable[str | None]]:
    async def handler(lease, recovery: RecoveryJobContext) -> str | None:
        await asyncio.to_thread(_execute_translate, settings, db_path, recovery, lease)
        return None

    return handler


class SynthesizeReject(HandlerUnavailable):
    """A SYNTHESIZE job refused before any segment is rendered."""

    def __init__(self, code: str) -> None:
        super().__init__(JobKind.SYNTHESIZE)
        self.code = code


def build_synthesize_handler(
    *,
    tts: TtsAdapter | None = None,
    audio_processor: AudioProcessor | None = None,
    allow_fake_tts: bool = False,
) -> Callable[..., Awaitable[str | None]]:
    """Render segment audio under the worker, one durable checkpoint per segment.

    The adapters are injected (and fake adapters must say so through allow_fake_tts)
    so offline tests exercise the real handler, worker, cache and artifact store
    without a network call. The database session and the artifact root come from the
    recovery context the worker built for this attempt, which is what makes the
    per-segment commits real checkpoints rather than bookkeeping.
    """

    async def handler(lease, recovery: RecoveryJobContext) -> str | None:
        await asyncio.to_thread(
            _execute_synthesize,
            recovery,
            lease,
            tts,
            audio_processor,
            allow_fake_tts,
        )
        return None

    return handler


def _execute_synthesize(
    recovery: RecoveryJobContext,
    lease,
    tts: TtsAdapter | None,
    audio_processor: AudioProcessor | None,
    allow_fake_tts: bool,
) -> None:
    job = recovery.runner.get(lease.job_id)
    try:
        plan = require_plan(JobKind.SYNTHESIZE, job.plan)
    except PlanRequiredError as exc:
        raise SynthesizeReject(exc.code) from exc
    chapter_id = job.chapter_id
    if not chapter_id:
        raise SynthesizeReject("SYNTHESIZE_CHAPTER_REQUIRED")

    session = recovery.session
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise SynthesizeReject("SYNTHESIZE_CHAPTER_NOT_FOUND")
    _validate_synthesize_plan(chapter, plan)
    # Close the validation read before the first provider call: no TTS call may run
    # inside an open SQLite transaction.
    session.commit()

    workflow = SpeechWorkflow(
        session,
        tts=tts,
        audio_processor=audio_processor,
        artifact_root=recovery.artifacts.artifact_root,
        allow_fake_tts=allow_fake_tts,
    )
    try:
        workflow.synthesize_segments(
            chapter_id,
            checkpoint=recovery,
            force_segment_ids=_planned_segment_ids(plan),
        )
    except TranslationApprovalRequired as exc:
        raise SynthesizeReject(f"SYNTHESIZE_{exc}") from exc
    except VoicePlanRequired as exc:
        raise SynthesizeReject(f"SYNTHESIZE_{exc}") from exc
    except TtsUnavailable as exc:
        raise SynthesizeReject(f"SYNTHESIZE_{exc}") from exc


def _validate_synthesize_plan(
    chapter: Chapter, plan: Mapping[str, object]
) -> None:
    """Fail closed before any render when the immutable plan no longer matches.

    The plan is written once at enqueue and never mutated, so an approved translation
    run, a voice plan or a source revision that moved after enqueue is a stale plan and
    the job is refused with a precise code instead of rendering something else.
    """
    approved_run_id = chapter.approved_translation_run_id
    if approved_run_id is None:
        raise SynthesizeReject("SYNTHESIZE_TRANSLATION_APPROVAL_REQUIRED")
    planned_run_id = plan.get("translationRunId")
    if planned_run_id is not None and str(planned_run_id) != str(approved_run_id):
        raise SynthesizeReject("SYNTHESIZE_TRANSLATION_STALE")

    voice_plan_id = chapter.active_voice_plan_id
    if voice_plan_id is None:
        raise SynthesizeReject("SYNTHESIZE_VOICE_PLAN_REQUIRED")
    planned_voice_plan_id = plan.get("voicePlanId")
    if planned_voice_plan_id is not None and str(planned_voice_plan_id) != str(voice_plan_id):
        raise SynthesizeReject("SYNTHESIZE_VOICE_PLAN_STALE")

    revision_id = plan.get("revisionId")
    if revision_id is not None and str(chapter.active_source_revision_id or "") != str(revision_id):
        raise SynthesizeReject("SYNTHESIZE_REVISION_STALE")


def _planned_segment_ids(plan: Mapping[str, object]) -> frozenset[str]:
    """Read the optional selective re-render list pinned in the execution plan."""
    requested = plan.get("forceSegmentIds")
    if not isinstance(requested, (list, tuple)):
        return frozenset()
    return frozenset(str(segment_id) for segment_id in requested)


def _database_path(settings: Settings, db_path: object | None) -> object:
    if db_path is not None:
        return db_path
    return settings.data_root / "studio.sqlite3"


def _execute_translate(
    settings: Settings, db_path: object | None, recovery: RecoveryJobContext, lease: object
) -> None:
    database_path = _database_path(settings, db_path)
    job = recovery.runner.get(lease.job_id)
    try:
        plan = require_plan(JobKind.TRANSLATE, job.plan)
    except PlanRequiredError as exc:
        raise TranslateReject(exc.code) from exc
    chapter_id = job.chapter_id
    if not chapter_id:
        raise TranslateReject("TRANSLATE_CHAPTER_REQUIRED")
    profile_id = str(plan["profileId"])

    engine = create_engine_for(database_path)
    try:
        factory = session_factory(engine)
        with factory() as session:
            chapter = session.get(Chapter, chapter_id)
            if chapter is None:
                raise TranslateReject("CHAPTER_NOT_FOUND")
            plan_revision = plan.get("revisionId")
            if plan_revision is not None and str(chapter.active_source_revision_id or "") != str(
                plan_revision
            ):
                raise TranslateReject("TRANSLATE_REVISION_STALE")
            profile = session.get(ProviderProfile, profile_id)
            if profile is None:
                raise TranslateReject("PROFILE_NOT_FOUND")
            if not profile.enabled:
                raise TranslateReject("PROFILE_DISABLED")
            adapter_name = str(profile.adapter_name or "")
    finally:
        engine.dispose()

    cloud_consent_id = str(plan["cloudConsentId"])
    budget_authorization_id = str(plan["budgetAuthorizationId"])
    if adapter_name.startswith("fake"):
        _run_fake_translate(database_path, chapter_id, cloud_consent_id, budget_authorization_id)
    elif adapter_name == "qwen-mt" or adapter_name.startswith("qwen"):
        _run_guarded_translate(
            settings, "qwen", chapter_id, profile_id, cloud_consent_id, budget_authorization_id
        )
    elif adapter_name.startswith("gemini"):
        _run_guarded_translate(
            settings, "gemini", chapter_id, profile_id, cloud_consent_id, budget_authorization_id
        )
    else:
        raise TranslateReject(f"TRANSLATE_PROVIDER_UNSUPPORTED:{adapter_name}")


def _run_fake_translate(
    database_path: object,
    chapter_id: str,
    cloud_consent_id: str,
    budget_authorization_id: str,
) -> None:
    from app.api.translation import CleanFakeTranslator
    from app.modules.translation.workflow import TranslationWorkflow

    engine = create_engine_for(database_path)
    try:
        with session_factory(engine)() as session:
            TranslationWorkflow(session, translator=CleanFakeTranslator()).enqueue_translation(
                chapter_id,
                cloud_consent_id=cloud_consent_id,
                budget_authorization_id=budget_authorization_id,
            )
    finally:
        engine.dispose()


def _run_guarded_translate(
    settings: Settings,
    provider: str,
    chapter_id: str,
    profile_id: str,
    cloud_consent_id: str,
    budget_authorization_id: str,
) -> None:
    def dispatch() -> None:
        if provider == "qwen":
            from app.api.translation import _qwen_workflow
            from app.modules.translation.draft_sink import DraftDeltaSinkFactory

            # J04 round 4: the worker persists provider deltas into the U04
            # workspace draft so the API can serve live offsets.
            sink = DraftDeltaSinkFactory(_database_path(settings, None))
            try:
                with _qwen_workflow(settings, chapter_id, profile_id, draft_sink=sink) as workflow:
                    workflow.enqueue_translation(
                        chapter_id,
                        cloud_consent_id=cloud_consent_id,
                        budget_authorization_id=budget_authorization_id,
                    )
            finally:
                sink.close()
            return
        from app.api.translation import _gemini_workflow

        with _gemini_workflow(settings, chapter_id, profile_id) as workflow:
            workflow.enqueue_translation(
                chapter_id,
                cloud_consent_id=cloud_consent_id,
                budget_authorization_id=budget_authorization_id,
            )

    engine = create_engine_for(_database_path(settings, None))
    try:
        with session_factory(engine)() as session:
            from app.modules.execution.breaker import with_breaker

            with_breaker(
                session,
                scope_key=f"provider:{provider}:profile:{profile_id}",
                fn=dispatch,
            )
    finally:
        engine.dispose()
