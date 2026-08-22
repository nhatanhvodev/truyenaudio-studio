from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
from enum import Enum
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.contracts import ArtifactKind, ArtifactStatus
from app.db.base import create_engine_for, session_factory
from app.db.models import Artifact, Chapter
from app.modules.speech.workflow import (
    AudioApprovalBlocked,
    AudioApprovalConflict,
    SpeechWorkflow,
    TranslationApprovalRequired,
    TtsUnavailable,
    VoicePlanRequired,
)
from app.providers.fake import FakeMp3AudioProcessor, FakeTts
from app.settings.config import Settings


class ConfigureSingleRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    preset_id: str = Field(alias="presetId")


class ApproveAudioRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    master_artifact_id: str = Field(alias="masterArtifactId")
    expected_sha256: str = Field(alias="expectedSha256")


class RenderAudioRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cloud_consent_id: str | None = Field(default=None, alias="cloudConsentId")
    budget_authorization_id: str | None = Field(default=None, alias="budgetAuthorizationId")


def create_audio_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/chapters/{chapter_id}/audio")
    active_settings = settings or Settings()

    def workflow_dependency() -> Iterator[SpeechWorkflow]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            fake_audio = os.getenv("STUDIO_FAKE_AUDIO") == "1"
            yield SpeechWorkflow(
                session,
                tts=FakeTts() if fake_audio else None,
                audio_processor=FakeMp3AudioProcessor() if fake_audio else None,
                artifact_root=active_settings.data_root / "artifacts",
                allow_fake_tts=fake_audio,
            )
        engine.dispose()

    @router.post("/configure-single")
    def configure_single(
        chapter_id: str,
        request: ConfigureSingleRequest,
        workflow: SpeechWorkflow = Depends(workflow_dependency),
    ) -> dict[str, object]:
        try:
            return _camel_payload(
                workflow.configure_single(chapter_id, request.preset_id)
            )
        except TranslationApprovalRequired as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/render")
    def render_audio(
        chapter_id: str,
        request: RenderAudioRequest | None = None,
        workflow: SpeechWorkflow = Depends(workflow_dependency),
    ) -> dict[str, object]:
        render_request = request or RenderAudioRequest()
        try:
            return _camel_payload(
                workflow.enqueue_render(
                    chapter_id,
                    cloud_consent_id=render_request.cloud_consent_id,
                    budget_authorization_id=render_request.budget_authorization_id,
                )
            )
        except VoicePlanRequired as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TranslationApprovalRequired as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TtsUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/approve")
    def approve_audio(
        chapter_id: str,
        request: ApproveAudioRequest,
        workflow: SpeechWorkflow = Depends(workflow_dependency),
    ) -> dict[str, object]:
        try:
            return _camel_payload(
                workflow.approve_audio(
                    chapter_id,
                    request.master_artifact_id,
                    expected_sha256=request.expected_sha256,
                )
            )
        except AudioApprovalBlocked as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AudioApprovalConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/status")
    def audio_status(chapter_id: str) -> dict[str, object]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        try:
            with factory() as session:
                chapter = session.get(Chapter, chapter_id)
                if chapter is None:
                    raise HTTPException(status_code=404, detail="chapter not found")
                artifact = None
                approved = False
                if chapter.approved_master_artifact_id:
                    artifact = session.get(
                        Artifact, chapter.approved_master_artifact_id
                    )
                    approved = artifact is not None
                if artifact is None:
                    artifact = (
                        session.execute(
                            select(Artifact)
                            .where(
                                Artifact.chapter_id == chapter_id,
                                Artifact.kind == ArtifactKind.MASTER_MP3.value,
                                Artifact.status == ArtifactStatus.READY.value,
                            )
                            .order_by(Artifact.updated_at.desc(), Artifact.id.desc())
                        )
                        .scalars()
                        .first()
                    )
                return {
                    "chapterId": chapter_id,
                    "masterArtifactId": artifact.id if artifact else None,
                    "masterSha256": artifact.sha256 if artifact else None,
                    "approved": approved,
                }
        finally:
            engine.dispose()

    return router


def _camel_payload(value: object) -> dict[str, object]:
    return _camelize(_convert(asdict(value)))


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
        return {_camel_key(key): _camelize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


def _camel_key(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)
