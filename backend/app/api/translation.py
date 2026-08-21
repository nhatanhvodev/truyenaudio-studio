from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contracts import TranslationRequest, TranslationResult, Usage, UsageUnit
from app.db.base import create_engine_for, session_factory
from app.modules.translation.workflow import (
    ApprovalBlocked,
    RevisionConflict,
    TranslationWorkflow,
)
from app.settings.config import Settings


class ReviseSegmentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    run_id: str = Field(alias="runId")
    target_text: str = Field(alias="targetText")
    expected_run_hash: str = Field(alias="expectedRunHash")


class ApproveTranslationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    run_id: str = Field(alias="runId")
    expected_run_hash: str = Field(alias="expectedRunHash")


def create_translation_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/chapters/{chapter_id}/translation")
    active_settings = settings or Settings()

    def workflow_dependency() -> Iterator[TranslationWorkflow]:
        yield from _workflow_dependency(active_settings)

    def fake_workflow_dependency() -> Iterator[TranslationWorkflow]:
        yield from _workflow_dependency(active_settings, translator=CleanFakeTranslator())

    @router.post("/fake")
    def run_fake_translation(
        chapter_id: str,
        workflow: TranslationWorkflow = Depends(fake_workflow_dependency),
    ) -> dict[str, object]:
        try:
            return _run_payload(workflow.enqueue_translation(chapter_id))
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
