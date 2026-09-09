"""Worker execution handlers wired to real domain services (task J01).

TRANSLATE handler executes the existing guarded translation workflow under
the worker. Cloud/model providers (qwen/gemini) reuse the API workflow
scopes (guarded adapter + fresh session); the local fake provider runs the
deterministic hanviet translator. Plan and revision are validated before any
dispatch; a rejection surfaces as a HandlerUnavailable subclass so the worker
fails the job with a precise, non-retryable code.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from app.contracts import JobKind
from app.db.base import create_engine_for, session_factory
from app.db.models import Chapter, ProviderProfile
from app.modules.jobs.handlers import PlanRequiredError, require_plan
from app.modules.jobs.recovery import RecoveryJobContext
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
    if provider == "qwen":
        from app.api.translation import _qwen_workflow

        with _qwen_workflow(settings, chapter_id, profile_id) as workflow:
            workflow.enqueue_translation(
                chapter_id,
                cloud_consent_id=cloud_consent_id,
                budget_authorization_id=budget_authorization_id,
            )
        return
    from app.api.translation import _gemini_workflow

    with _gemini_workflow(settings, chapter_id, profile_id) as workflow:
        workflow.enqueue_translation(
            chapter_id,
            cloud_consent_id=cloud_consent_id,
            budget_authorization_id=budget_authorization_id,
        )
