from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException
import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.contracts import TranslationRequest, TranslationResult, Usage, UsageUnit
from app.db.base import create_engine_for, session_factory
from app.db.models import ProviderProfile
from app.modules.budgets.guard import BudgetGuard
from app.modules.compliance.cloud import CloudCallBlocked, CloudCallGuard
from app.modules.translation.workflow import (
    ApprovalBlocked,
    RevisionConflict,
    TranslationWorkflow,
)
from app.providers.qwen_mt import QwenMtAdapter, Secret
from app.settings.config import Settings

QWEN_DEFAULT_ENDPOINT = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation"


class ReviseSegmentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    run_id: str = Field(alias="runId")
    target_text: str = Field(alias="targetText")
    expected_run_hash: str = Field(alias="expectedRunHash")


class ApproveTranslationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    run_id: str = Field(alias="runId")
    expected_run_hash: str = Field(alias="expectedRunHash")


class QwenTranslationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cloud_consent_id: str | None = Field(default=None, alias="cloudConsentId")
    budget_authorization_id: str | None = Field(
        default=None, alias="budgetAuthorizationId"
    )


def create_translation_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/chapters/{chapter_id}/translation")
    active_settings = settings or Settings()

    def workflow_dependency() -> Iterator[TranslationWorkflow]:
        yield from _workflow_dependency(active_settings)

    def fake_workflow_dependency() -> Iterator[TranslationWorkflow]:
        yield from _workflow_dependency(
            active_settings, translator=CleanFakeTranslator()
        )

    @router.post("/fake")
    def run_fake_translation(
        chapter_id: str,
        workflow: TranslationWorkflow = Depends(fake_workflow_dependency),
    ) -> dict[str, object]:
        try:
            return _run_payload(workflow.enqueue_translation(chapter_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/qwen")
    def run_qwen_translation(
        chapter_id: str,
        request: QwenTranslationRequest,
    ) -> dict[str, object]:
        if not request.cloud_consent_id:
            raise HTTPException(status_code=422, detail="CLOUD_CONSENT_REQUIRED")
        if not request.budget_authorization_id:
            raise HTTPException(status_code=422, detail="BUDGET_AUTHORIZATION_REQUIRED")
        try:
            with _qwen_workflow(active_settings, chapter_id) as workflow:
                return _run_payload(
                    workflow.enqueue_translation(
                        chapter_id,
                        cloud_consent_id=request.cloud_consent_id,
                        budget_authorization_id=request.budget_authorization_id,
                    )
                )
        except CloudCallBlocked as exc:
            raise HTTPException(status_code=403, detail=",".join(exc.reasons)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("")
    def read_translation(
        chapter_id: str,
        workflow: TranslationWorkflow = Depends(workflow_dependency),
    ) -> dict[str, object]:
        try:
            return _run_payload(workflow.current_translation(chapter_id))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch("/segments/{source_segment_id}")
    def revise_segment(
        chapter_id: str,
        source_segment_id: str,
        request: ReviseSegmentRequest,
        workflow: TranslationWorkflow = Depends(workflow_dependency),
    ) -> dict[str, object]:
        try:
            run = workflow.revise_segment(
                chapter_id,
                request.run_id,
                source_segment_id,
                request.target_text,
                expected_run_hash=request.expected_run_hash,
            )
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _run_payload(run)

    @router.post("/approve")
    def approve_translation(
        chapter_id: str,
        request: ApproveTranslationRequest,
        workflow: TranslationWorkflow = Depends(workflow_dependency),
    ) -> dict[str, object]:
        try:
            run = workflow.approve_revision(
                chapter_id,
                request.run_id,
                request.expected_run_hash,
            )
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ApprovalBlocked as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _run_payload(run)

    return router


def _workflow_dependency(
    active_settings: Settings,
    translator: object | None = None,
) -> Iterator[TranslationWorkflow]:
    engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
    factory = session_factory(engine)
    with factory() as session:
        yield TranslationWorkflow(session, translator=translator)
    engine.dispose()


class _qwen_workflow:
    def __init__(self, active_settings: Settings, chapter_id: str) -> None:
        self.active_settings = active_settings
        self.chapter_id = chapter_id
        self.engine = None
        self.session_cm = None

    def __enter__(self) -> TranslationWorkflow:
        try:
            self.engine = create_engine_for(
                self.active_settings.data_root / "studio.sqlite3"
            )
            factory = session_factory(self.engine)
            self.session_cm = factory()
            session = self.session_cm.__enter__()
            profile = session.scalar(
                select(ProviderProfile)
                .where(
                    ProviderProfile.provider_kind == "TRANSLATOR",
                    ProviderProfile.adapter_name == "qwen",
                    ProviderProfile.enabled.is_(True),
                )
                .order_by(ProviderProfile.id)
            )
            if profile is None:
                raise ValueError("QWEN_PROVIDER_PROFILE_REQUIRED")
            if not profile.model or not profile.region or not profile.secret_ref:
                raise ValueError("QWEN_PROVIDER_PROFILE_INCOMPLETE")
            adapter = QwenMtAdapter(
                httpx.AsyncClient(),
                profile.model,
                profile.region,
                Secret.from_ref(profile.secret_ref),
                cloud_guard=CloudCallGuard(session, BudgetGuard(session)),
                project_id=_chapter_project_id(session, self.chapter_id),
                provider_profile_id=profile.id,
                endpoint=str(
                    (profile.config_json or {}).get("endpoint") or QWEN_DEFAULT_ENDPOINT
                ),
            )
            return TranslationWorkflow(session, translator=adapter)
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.session_cm is not None:
            self.session_cm.__exit__(exc_type, exc, tb)
        if self.engine is not None:
            self.engine.dispose()


def _chapter_project_id(session, chapter_id: str) -> str:
    from app.db.models import Chapter

    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise ValueError("CHAPTER_NOT_FOUND")
    return chapter.project_id


class CleanFakeTranslator:
    def capabilities(self) -> dict[str, object]:
        return {
            "provider": "fake",
            "model": "fake-ui-clean",
            "region": "local",
            "network": False,
        }

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        target = "Chuong 1. Lam Dong noi xin chao."
        return TranslationResult(
            target_text=target,
            provider="fake",
            model="fake-ui-clean",
            provider_version="1",
            usage=(Usage(UsageUnit.CHARACTER.value, len(request.source_text)),),
        )


def _run_payload(run: object) -> dict[str, object]:
    data = _convert(asdict(run))
    data["run"] = {
        "id": data.pop("id"),
        "chapterId": data.pop("chapter_id"),
        "sourceRevisionId": data.pop("source_revision_id"),
        "status": data.pop("status"),
        "sha256": data.pop("sha256"),
        "providerProfileId": data.pop("provider_profile_id"),
        "model": data.pop("model"),
        "glossaryRevisionHash": data.pop("glossary_revision_hash"),
        "storyMemoryRevisionHash": data.pop("story_memory_revision_hash"),
    }
    data["segments"] = [
        {
            "id": segment["id"],
            "sourceSegmentId": segment["source_segment_id"],
            "sourceText": segment["source_text"],
            "targetText": segment["target_text"],
            "targetSha256": segment["target_sha256"],
            "cacheKey": segment["cache_key"],
            "wasCacheHit": segment["was_cache_hit"],
            "manuallyEdited": segment["manually_edited"],
        }
        for segment in data["segments"]
    ]
    data["issues"] = [
        {
            "id": issue["id"],
            "category": issue["category"],
            "severity": issue["severity"],
            "status": issue["status"],
            "evidence": issue["evidence"],
            "suggestion": issue["suggestion"],
            "ruleOrModel": issue["rule_or_model"],
            "sourceSegmentId": issue["source_segment_id"],
        }
        for issue in data["issues"]
    ]
    return data


def _convert(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _convert(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_convert(item) for item in value]
    return value
