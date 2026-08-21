from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
from enum import Enum
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.contracts import ExportKind
from app.db.base import create_engine_for, session_factory
from app.modules.exports.schemas import PublicationMetadata
from app.modules.exports.workflow import BundleVerificationError, ExportWorkflow
from app.providers.fake import FakeMp3AudioProcessor
from app.settings.config import Settings


class PublicationMetadataRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    episode_title: str = Field(alias="episodeTitle")
    suggested_episode_number: int = Field(alias="suggestedEpisodeNumber")
    is_premium: bool = Field(alias="isPremium")


def create_exports_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/chapters/{chapter_id}/exports")
    active_settings = settings or Settings()

    def workflow_dependency() -> Iterator[ExportWorkflow]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield ExportWorkflow(
                session,
                artifact_root=active_settings.data_root / "artifacts",
                audio_processor=FakeMp3AudioProcessor()
                if os.getenv("STUDIO_FAKE_AUDIO") == "1"
                else None,
            )
        engine.dispose()

    @router.get("/gate")
    def gate(chapter_id: str, workflow: ExportWorkflow = Depends(workflow_dependency)) -> dict[str, object]:
        try:
            return _camel_payload(workflow.evaluate_gate(chapter_id, ExportKind.PUBLICATION_BUNDLE))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/private")
    def private_archive(chapter_id: str, workflow: ExportWorkflow = Depends(workflow_dependency)) -> dict[str, object]:
        try:
            return _camel_payload(workflow.build_private_archive(chapter_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/publication")
    def publication_bundle(
        chapter_id: str,
        request: PublicationMetadataRequest,
        workflow: ExportWorkflow = Depends(workflow_dependency),
    ) -> dict[str, object]:
        try:
            return _camel_payload(
                workflow.build_publication_bundle(
                    chapter_id,
                    PublicationMetadata(
                        request.episode_title,
                        request.suggested_episode_number,
                        request.is_premium,
                    ),
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except BundleVerificationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


def _camel_payload(value: object) -> dict[str, object]:
    return _camelize(_convert(asdict(value)))


def _convert(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "as_posix"):
        return str(value)
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
