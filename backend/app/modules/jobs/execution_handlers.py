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

REVIEW and SUMMARIZE (task J01, final slice) follow the TRANSLATE skeleton -
require_plan, chapter/revision/profile validation, then an explicit fake vs
cloud branch - and each branch is honest about what it can do offline:

* REVIEW runs the frozen deterministic rule set over the chapter's current
  REVIEW run and records the issues it finds. The local ("fake") profile
  adapter is the only adapter wired to that path today; a cloud reviewer
  (qwen/gemini) is refused with REVIEW_CLOUD_PROVIDER_NOT_WIRED rather than
  silently downgraded to the local rules.
* SUMMARIZE writes one story-memory CANDIDATE recap per chapter from the
  chapter's newest translation run (falling back to the normalized source)
  with no model call. A cloud summarizer is refused with
  SUMMARIZE_REQUIRES_CLOUD_PROVIDER.

Both handlers are idempotent: replaying a job never duplicates a QA issue or
re-proposes a story-memory recap that was already recorded or decided, so an
interrupted or retried job converges on the same database state.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import AudioProcessor, JobKind, QaStatus, RunStatus, TtsAdapter, new_id
from app.db.base import create_engine_for, session_factory
from app.db.models import (
    Chapter,
    ProviderProfile,
    QaIssue,
    SourceRevision,
    SourceSegment,
    StoryMemoryEntry as StoryMemoryRow,
    TranslationRun,
    TranslationSegment,
)
from app.modules.jobs.handlers import PlanRequiredError, require_plan
from app.modules.translation.glossary import locked_rules_for_chapter
from app.modules.translation.qa import run_deterministic_qa
from app.modules.translation.story_memory import (
    STATUS_APPROVED,
    STATUS_CANDIDATE,
    STATUS_REJECTED,
    StoryMemoryService,
)
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


# Provider routing shared by the REVIEW and SUMMARIZE handlers. A profile whose
# adapter is not listed here is refused before any work happens, so an unknown
# adapter can never be mistaken for the offline one.
FAKE_PROVIDER = "fake"
CLOUD_PROVIDER = "cloud"
UNSUPPORTED_PROVIDER = "unsupported"
LOCAL_SUMMARY_CHAR_LIMIT = 280


class ReviewReject(HandlerUnavailable):
    """A REVIEW job refused before any QA issue is written."""

    def __init__(self, code: str) -> None:
        super().__init__(JobKind.REVIEW)
        self.code = code


def build_review_handler(
    settings: Settings, *, db_path: object | None = None
) -> Callable[..., Awaitable[str | None]]:
    async def handler(lease, recovery: RecoveryJobContext) -> str | None:
        await asyncio.to_thread(_execute_review, settings, db_path, recovery, lease)
        return None

    return handler


def _execute_review(
    settings: Settings, db_path: object | None, recovery: RecoveryJobContext, lease: object
) -> None:
    job = recovery.runner.get(lease.job_id)
    try:
        plan = require_plan(JobKind.REVIEW, job.plan)
    except PlanRequiredError as exc:
        raise ReviewReject(exc.code) from exc
    chapter_id = job.chapter_id
    if not chapter_id:
        raise ReviewReject("REVIEW_CHAPTER_REQUIRED")
    profile_id = str(plan["profileId"])
    database_path = _database_path(settings, db_path)

    engine = create_engine_for(database_path)
    try:
        with session_factory(engine)() as session:
            adapter_name = _validate_chapter_profile(
                session,
                chapter_id=chapter_id,
                profile_id=profile_id,
                plan=plan,
                reject=ReviewReject,
                prefix="REVIEW",
            )
    finally:
        engine.dispose()

    route = _provider_route(adapter_name)
    if route == FAKE_PROVIDER:
        recovery.raise_if_cancel_requested()
        _run_fake_review(database_path, chapter_id)
        return
    if route == CLOUD_PROVIDER:
        # A cloud reviewer is not wired to the worker yet: dispatching the local
        # rule set instead would report a model review that never happened.
        raise ReviewReject("REVIEW_CLOUD_PROVIDER_NOT_WIRED")
    raise ReviewReject(f"REVIEW_PROVIDER_UNSUPPORTED:{adapter_name}")


def _run_fake_review(database_path: object, chapter_id: str) -> None:
    """Record the issues the frozen rule set finds in the current REVIEW run.

    The local path is deterministic and offline: no provider is called and no
    translation text is written. Issues are keyed by (source segment, rule), so
    replaying a REVIEW job never duplicates an issue and never resurrects one the
    editor already dismissed or accepted as risk.
    """
    engine = create_engine_for(database_path)
    try:
        with session_factory(engine)() as session:
            chapter = session.get(Chapter, chapter_id)
            if chapter is None:
                raise ReviewReject("REVIEW_CHAPTER_NOT_FOUND")
            run = _current_review_run(session, chapter_id)
            if run is None:
                raise ReviewReject("REVIEW_RUN_NOT_FOUND")
            rules = locked_rules_for_chapter(session, chapter.project_id, chapter.ordinal)
            locked_terms = tuple((rule.source_term, rule.target_term) for rule in rules)
            forbidden_forms = tuple(
                (rule.source_term, rule.forbidden_forms)
                for rule in rules
                if rule.forbidden_forms
            )
            recorded = {
                (row[0], row[1])
                for row in session.execute(
                    select(QaIssue.source_segment_id, QaIssue.rule_or_model).where(
                        QaIssue.translation_run_id == run.id
                    )
                )
            }
            for segment in _run_segments(session, run.id):
                source = session.get(SourceSegment, segment.source_segment_id)
                if source is None:
                    raise ReviewReject("REVIEW_SOURCE_SEGMENT_NOT_FOUND")
                drafts = run_deterministic_qa(
                    source.source_text,
                    segment.target_text,
                    locked_terms,
                    forbidden_forms,
                )
                for draft in drafts:
                    if (segment.source_segment_id, draft.rule_or_model) in recorded:
                        continue
                    session.add(
                        QaIssue(
                            id=new_id(),
                            chapter_id=run.chapter_id,
                            translation_run_id=run.id,
                            category=draft.category.value,
                            severity=draft.severity.value,
                            status=QaStatus.OPEN.value,
                            source_segment_id=segment.source_segment_id,
                            evidence=draft.evidence,
                            suggestion=draft.suggestion,
                            rule_or_model=draft.rule_or_model,
                        )
                    )
            session.commit()
    finally:
        engine.dispose()


class SummarizeReject(HandlerUnavailable):
    """A SUMMARIZE job refused before any story-memory candidate is written."""

    def __init__(self, code: str) -> None:
        super().__init__(JobKind.SUMMARIZE)
        self.code = code


def build_summarize_handler(
    settings: Settings, *, db_path: object | None = None
) -> Callable[..., Awaitable[str | None]]:
    async def handler(lease, recovery: RecoveryJobContext) -> str | None:
        await asyncio.to_thread(_execute_summarize, settings, db_path, recovery, lease)
        return None

    return handler


def _execute_summarize(
    settings: Settings, db_path: object | None, recovery: RecoveryJobContext, lease: object
) -> None:
    job = recovery.runner.get(lease.job_id)
    try:
        plan = require_plan(JobKind.SUMMARIZE, job.plan)
    except PlanRequiredError as exc:
        raise SummarizeReject(exc.code) from exc
    chapter_id = job.chapter_id
    if not chapter_id:
        raise SummarizeReject("SUMMARIZE_CHAPTER_REQUIRED")
    profile_id = str(plan["profileId"])
    database_path = _database_path(settings, db_path)

    engine = create_engine_for(database_path)
    try:
        with session_factory(engine)() as session:
            adapter_name = _validate_chapter_profile(
                session,
                chapter_id=chapter_id,
                profile_id=profile_id,
                plan=plan,
                reject=SummarizeReject,
                prefix="SUMMARIZE",
            )
    finally:
        engine.dispose()

    route = _provider_route(adapter_name)
    if route == FAKE_PROVIDER:
        recovery.raise_if_cancel_requested()
        _run_fake_summarize(database_path, chapter_id)
        return
    if route == CLOUD_PROVIDER:
        raise SummarizeReject("SUMMARIZE_REQUIRES_CLOUD_PROVIDER")
    raise SummarizeReject(f"SUMMARIZE_PROVIDER_UNSUPPORTED:{adapter_name}")


def _run_fake_summarize(database_path: object, chapter_id: str) -> None:
    """Write one deterministic story-memory CANDIDATE recap, offline.

    The recap is extracted from the chapter text with no model call, and it is
    always a CANDIDATE: only StoryMemoryService.approve, which demands an
    APPROVED run plus evidence segments, can put it into translation context. A
    recap that was already recorded or decided is never re-proposed, so a retried
    job converges instead of piling up revisions.
    """
    engine = create_engine_for(database_path)
    try:
        with session_factory(engine)() as session:
            chapter = session.get(Chapter, chapter_id)
            if chapter is None:
                raise SummarizeReject("SUMMARIZE_CHAPTER_NOT_FOUND")
            revision = (
                session.get(SourceRevision, chapter.active_source_revision_id)
                if chapter.active_source_revision_id
                else None
            )
            summary = _local_summary(_summary_source(session, chapter, revision))
            if not summary:
                raise SummarizeReject("SUMMARIZE_SOURCE_TEXT_REQUIRED")
            entity_key = f"chapter:{chapter.ordinal}"
            if _memory_entry_exists(session, chapter.project_id, entity_key, summary):
                return
            StoryMemoryService(session).create_candidate(
                chapter.project_id,
                entity_key=entity_key,
                entity_type="CHAPTER",
                summary=summary,
                valid_from_ordinal=chapter.ordinal,
            )
    finally:
        engine.dispose()


def _summary_source(
    session: Session, chapter: Chapter, revision: SourceRevision | None
) -> str:
    """Prefer the newest translated text, fall back to the normalized source."""
    run = session.scalar(
        select(TranslationRun)
        .where(
            TranslationRun.chapter_id == chapter.id,
            TranslationRun.status.in_(
                (RunStatus.REVIEW.value, RunStatus.APPROVED.value)
            ),
        )
        .order_by(TranslationRun.created_at.desc(), TranslationRun.id.desc())
    )
    if run is not None:
        targets = [
            segment.target_text.strip()
            for segment in _run_segments(session, run.id)
            if segment.target_text.strip()
        ]
        if targets:
            return "\n".join(targets)
    if revision is None:
        return ""
    return revision.normalized_text or ""


def _local_summary(text: str) -> str:
    """Collapse whitespace and clip the recap to a deterministic length."""
    return " ".join(text.split())[:LOCAL_SUMMARY_CHAR_LIMIT]


def _memory_entry_exists(
    session: Session, project_id: str, entity_key: str, summary: str
) -> bool:
    """True when this exact recap was already proposed or decided for the entity."""
    return (
        session.scalar(
            select(StoryMemoryRow.id)
            .where(
                StoryMemoryRow.project_id == project_id,
                StoryMemoryRow.entity_key == entity_key,
                StoryMemoryRow.summary == summary,
                StoryMemoryRow.status.in_(
                    (STATUS_CANDIDATE, STATUS_APPROVED, STATUS_REJECTED)
                ),
            )
            .limit(1)
        )
        is not None
    )


def _current_review_run(session: Session, chapter_id: str) -> TranslationRun | None:
    """The newest REVIEW run of the chapter, or None when the chapter has none."""
    return session.scalar(
        select(TranslationRun)
        .where(
            TranslationRun.chapter_id == chapter_id,
            TranslationRun.status == RunStatus.REVIEW.value,
        )
        .order_by(TranslationRun.created_at.desc(), TranslationRun.id.desc())
    )


def _run_segments(session: Session, run_id: str) -> tuple[TranslationSegment, ...]:
    return tuple(
        session.scalars(
            select(TranslationSegment)
            .where(TranslationSegment.translation_run_id == run_id)
            .join(SourceSegment, SourceSegment.id == TranslationSegment.source_segment_id)
            .order_by(SourceSegment.segment_index, TranslationSegment.id)
        ).all()
    )


def _validate_chapter_profile(
    session: Session,
    *,
    chapter_id: str,
    profile_id: str,
    plan: Mapping[str, object],
    reject: Callable[[str], HandlerUnavailable],
    prefix: str,
) -> str:
    """Fail closed before any dispatch; returns the profile adapter name.

    The plan is written once at enqueue and never mutated, so a source revision
    that moved after enqueue is a stale plan and the job is refused instead of
    reviewing or summarizing something the operator never approved.
    """
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise reject(f"{prefix}_CHAPTER_NOT_FOUND")
    plan_revision = plan.get("revisionId")
    if plan_revision is not None and str(chapter.active_source_revision_id or "") != str(
        plan_revision
    ):
        raise reject(f"{prefix}_REVISION_STALE")
    profile = session.get(ProviderProfile, profile_id)
    if profile is None:
        raise reject(f"{prefix}_PROFILE_NOT_FOUND")
    if not profile.enabled:
        raise reject(f"{prefix}_PROFILE_DISABLED")
    return str(profile.adapter_name or "")


def _provider_route(adapter_name: str) -> str:
    """Classify a profile adapter into the fake, cloud or unsupported branch."""
    if adapter_name.startswith("fake"):
        return FAKE_PROVIDER
    if adapter_name == "qwen-mt" or adapter_name.startswith("qwen"):
        return CLOUD_PROVIDER
    if adapter_name.startswith("gemini"):
        return CLOUD_PROVIDER
    return UNSUPPORTED_PROVIDER
