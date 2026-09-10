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
from app.db.models import BudgetAuthorization as BudgetAuthorizationRow
from app.db.models import Chapter, ProviderProfile, SourceSegment
from app.modules.budgets.guard import BudgetGuard
from app.modules.compliance.cloud import CloudCallBlocked, CloudCallGuard
from app.modules.security.credentials import CredentialUnavailable
from app.modules.security.model_identifier import validate_model_identifier
from app.modules.translation.hanviet import convert_hanviet
from app.modules.translation.context_trace import build_context_trace
from app.modules.translation.workflow import (
    ApprovalBlocked,
    RevisionConflict,
    TranslationWorkflow,
)
from app.providers.gemini_mt import GeminiMtAdapter
from app.providers.qwen_mt import QWEN_ENDPOINT, QwenMtAdapter, Secret, canonical_qwen_endpoint
from app.providers.registry import ProviderRegistry, RegistryAuthorization
from app.settings.config import Settings

class ReviseSegmentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    run_id: str = Field(alias="runId")
    target_text: str = Field(alias="targetText")
    expected_run_hash: str = Field(alias="expectedRunHash")


class ApproveTranslationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    run_id: str = Field(alias="runId")
    expected_run_hash: str = Field(alias="expectedRunHash")
    force: bool = Field(default=False, alias="force")


class QwenTranslationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    profile_id: str = Field(alias="profileId", min_length=1)
    cloud_consent_id: str | None = Field(default=None, alias="cloudConsentId")
    budget_authorization_id: str | None = Field(
        default=None, alias="budgetAuthorizationId"
    )


class GeminiTranslationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    profile_id: str = Field(alias="profileId", min_length=1)
    cloud_consent_id: str | None = Field(default=None, alias="cloudConsentId")
    budget_authorization_id: str | None = Field(
        default=None, alias="budgetAuthorizationId"
    )


class TranslationQuoteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    profile_id: str = Field(alias="profileId", min_length=1)
    cloud_consent_id: str = Field(alias="cloudConsentId", min_length=1)
    category: str = "REGULAR"


def create_translation_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/chapters/{chapter_id}/translation")
    active_settings = settings or Settings()

    def workflow_dependency() -> Iterator[TranslationWorkflow]:
        yield from _workflow_dependency(active_settings)

    def fake_workflow_dependency() -> Iterator[TranslationWorkflow]:
        yield from _workflow_dependency(
            active_settings, translator=CleanFakeTranslator()
        )

    def trace_session_dependency() -> Iterator[object]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield session
        engine.dispose()

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
            with _qwen_workflow(
                active_settings, chapter_id, request.profile_id
            ) as workflow:
                return _run_payload(
                    workflow.enqueue_translation(
                        chapter_id,
                        cloud_consent_id=request.cloud_consent_id,
                        budget_authorization_id=request.budget_authorization_id,
                    )
                )
        except CloudCallBlocked as exc:
            raise HTTPException(status_code=403, detail=",".join(exc.reasons)) from exc
        except CredentialUnavailable as exc:
            raise HTTPException(status_code=503, detail="KEYRING_UNAVAILABLE") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=_public_provider_error(str(exc))) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/gemini")
    def run_gemini_translation(
        chapter_id: str,
        request: GeminiTranslationRequest,
    ) -> dict[str, object]:
        if not request.cloud_consent_id:
            raise HTTPException(status_code=422, detail="CLOUD_CONSENT_REQUIRED")
        if not request.budget_authorization_id:
            raise HTTPException(status_code=422, detail="BUDGET_AUTHORIZATION_REQUIRED")
        try:
            with _gemini_workflow(
                active_settings, chapter_id, request.profile_id
            ) as workflow:
                return _run_payload(
                    workflow.enqueue_translation(
                        chapter_id,
                        cloud_consent_id=request.cloud_consent_id,
                        budget_authorization_id=request.budget_authorization_id,
                    )
                )
        except CloudCallBlocked as exc:
            raise HTTPException(status_code=403, detail=",".join(exc.reasons)) from exc
        except CredentialUnavailable as exc:
            raise HTTPException(status_code=503, detail="KEYRING_UNAVAILABLE") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=_public_provider_error(str(exc))) from exc

    @router.post("/quote")
    def quote_translation(
        chapter_id: str,
        request: TranslationQuoteRequest,
    ) -> dict[str, object]:
        try:
            return _translation_quote_payload(
                active_settings,
                chapter_id,
                request.profile_id,
                request.cloud_consent_id,
                request.category,
            )
        except CloudCallBlocked as exc:
            raise HTTPException(status_code=403, detail=",".join(exc.reasons)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/context-trace")
    def read_context_trace(
        chapter_id: str,
        session: object = Depends(trace_session_dependency),
    ) -> dict[str, object]:
        """U06: what the latest run used (glossary/memory/characters) + staleness."""
        try:
            trace = build_context_trace(session, chapter_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _camelize(trace)

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
                force=request.force,
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
    def __init__(
        self,
        active_settings: Settings,
        chapter_id: str,
        profile_id: str,
        draft_sink: object | None = None,
    ) -> None:
        self.active_settings = active_settings
        self.chapter_id = chapter_id
        self.profile_id = profile_id
        self.draft_sink = draft_sink
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
            profile = _resolve_translation_profile(session, self.profile_id, {"qwen"})
            if not profile.model or not profile.region or not profile.secret_ref:
                raise ValueError("QWEN_PROVIDER_PROFILE_INCOMPLETE")
            model = validate_model_identifier(profile.model)
            endpoint = _qwen_endpoint_from_config(profile.config_json or {})
            adapter = QwenMtAdapter(
                httpx.AsyncClient(),
                model,
                profile.region,
                Secret.from_ref(profile.secret_ref),
                cloud_guard=CloudCallGuard(session, BudgetGuard(session)),
                project_id=_chapter_project_id(session, self.chapter_id),
                provider_profile_id=profile.id,
                dispatch_registry=ProviderRegistry(profile_revision_resolver=_db_profile_revision_resolver(session)),
                dispatch_authorization=RegistryAuthorization(profile.id, profile.revision, model),
                endpoint=endpoint,
            )
            return TranslationWorkflow(session, translator=adapter, draft_sink=self.draft_sink)
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.session_cm is not None:
            self.session_cm.__exit__(exc_type, exc, tb)
        if self.engine is not None:
            self.engine.dispose()


class _gemini_workflow:
    def __init__(
        self,
        active_settings: Settings,
        chapter_id: str,
        profile_id: str,
    ) -> None:
        self.active_settings = active_settings
        self.chapter_id = chapter_id
        self.profile_id = profile_id
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
            profile = _resolve_translation_profile(session, self.profile_id, {"gemini", "gemini_mt"})
            if not profile.model or not profile.secret_ref:
                raise ValueError("GEMINI_PROVIDER_PROFILE_INCOMPLETE")
            model = validate_model_identifier(profile.model)
            adapter = GeminiMtAdapter(
                api_key_ref=profile.secret_ref,
                model=model,
                http_client=httpx.AsyncClient(),
                cloud_guard=CloudCallGuard(session, BudgetGuard(session)),
                project_id=_chapter_project_id(session, self.chapter_id),
                provider_profile_id=profile.id,
                dispatch_registry=ProviderRegistry(profile_revision_resolver=_db_profile_revision_resolver(session)),
                dispatch_authorization=RegistryAuthorization(profile.id, profile.revision, model),
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
    chapter = session.get(Chapter, chapter_id)
    if chapter is None:
        raise ValueError("CHAPTER_NOT_FOUND")
    return chapter.project_id


def _translation_quote_payload(
    active_settings: Settings,
    chapter_id: str,
    profile_id: str,
    cloud_consent_id: str,
    category: str,
) -> dict[str, object]:
    engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
    factory = session_factory(engine)
    try:
        with factory() as session:
            chapter = session.get(Chapter, chapter_id)
            if chapter is None:
                raise ValueError("CHAPTER_NOT_FOUND")
            profile = _resolve_translation_profile(session, profile_id, {"gemini", "gemini_mt", "qwen", "qwen-mt", "qwen_mt"})
            if not profile.model or not profile.secret_ref:
                raise ValueError("PROVIDER_PROFILE_INCOMPLETE")
            validate_model_identifier(profile.model)
            estimated_usage = _estimated_translation_usage(session, chapter, profile)
            decision = CloudCallGuard(session, BudgetGuard(session)).reserve(
                project_id=chapter.project_id,
                provider_profile_id=profile.id,
                operation_id=_translation_operation_id(chapter.id),
                estimated_usage=estimated_usage,
                category=category,
                cloud_consent_id=cloud_consent_id,
                stage="TRANSLATE",
            )
            if not decision.allowed or decision.authorization_id is None:
                raise CloudCallBlocked(decision.reasons)
            row = session.get(BudgetAuthorizationRow, decision.authorization_id)
            if row is None:
                raise ValueError("BUDGET_AUTHORIZATION_INVALID")
            return {
                "budgetAuthorizationId": row.id,
                "operationId": row.operation_id,
                "category": row.category,
                "stage": row.stage,
                "estimateVnd": row.estimate_vnd,
                "contingencyVnd": row.contingency_vnd,
                "totalVnd": row.estimate_vnd + row.contingency_vnd,
                "expiresAt": row.expires_at.isoformat(),
                "rateCardIds": row.rate_card_ids_json or (),
                "profileId": row.provider_profile_id,
                "profileRevision": row.provider_profile_revision,
                "cloudConsentId": row.cloud_consent_id,
                "planHash": row.plan_hash,
                "quoteHash": row.quote_hash,
                "warnings": decision.warnings,
            }
    finally:
        engine.dispose()


def _estimated_translation_usage(
    session,
    chapter: Chapter,
    profile: ProviderProfile,
) -> tuple[Usage, ...]:
    if not chapter.active_source_revision_id:
        raise ValueError("SOURCE_REVISION_NOT_FOUND")
    segments = session.scalars(
        select(SourceSegment).where(SourceSegment.source_revision_id == chapter.active_source_revision_id)
    ).all()
    if not segments:
        raise ValueError("SOURCE_SEGMENTS_NOT_FOUND")
    source_characters = sum(len(segment.source_text) for segment in segments)
    unit = UsageUnit.INPUT_TOKEN.value if profile.adapter_name in {"qwen", "qwen-mt", "qwen_mt"} else UsageUnit.CHARACTER.value
    return (Usage(unit, source_characters),)


def _translation_operation_id(chapter_id: str) -> str:
    return f"translate:{chapter_id}"


def _resolve_translation_profile(session, profile_id: str, adapter_names: set[str]) -> ProviderProfile:
    profile = session.get(ProviderProfile, profile_id)
    if profile is None:
        raise ValueError("PROVIDER_PROFILE_NOT_FOUND")
    if profile.provider_kind != "TRANSLATOR" or profile.adapter_name not in adapter_names:
        raise ValueError("PROVIDER_PROFILE_KIND_MISMATCH")
    if not profile.enabled:
        raise ValueError("PROVIDER_PROFILE_DISABLED")
    return profile


def _qwen_endpoint_from_config(config: dict[str, object]) -> str:
    return canonical_qwen_endpoint(config.get("endpoint", QWEN_ENDPOINT))


def _public_provider_error(value: str) -> str:
    allowed = {
        "GEMINI_PROVIDER_UNAVAILABLE",
        "GEMINI_RATE_LIMIT_EXCEEDED",
        "QWEN_PROVIDER_REJECTED",
        "QWEN_PROVIDER_INVALID_RESPONSE",
    }
    return value if value in allowed else "PROVIDER_UNAVAILABLE"


def _db_profile_revision_resolver(session):
    def resolve(profile_id: str) -> int | None:
        return session.scalar(select(ProviderProfile.revision).where(ProviderProfile.id == profile_id))

    return resolve


class CleanFakeTranslator:
    def capabilities(self) -> dict[str, object]:
        return {
            "provider": "fake",
            "model": "fake-hanviet-v2",
            "provider_version": "2",
            "region": "local",
            "network": False,
        }

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        target = convert_hanviet(request.source_text, request.terms)
        return TranslationResult(
            target_text=target,
            provider="fake",
            model="fake-hanviet-v2",
            provider_version="2",
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


def _camelize(value: object) -> object:
    if isinstance(value, dict):
        return {_camel_key(str(key)): _camelize(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_camelize(item) for item in value]
    return value


def _camel_key(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)
