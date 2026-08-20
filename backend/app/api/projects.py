from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.contracts import ChapterState, ImportKind, RightsStatus, SourceType
from app.db.base import create_engine_for, session_factory
from app.modules.artifacts.store import ArtifactStore
from app.modules.projects.state_machine import InvalidChapterTransition
from app.modules.projects.workflow import CreateProject, ImportChapters, ProjectWorkflow
from app.settings.config import Settings


class CreateProjectRequest(BaseModel):
    title: str
    slug: str
    source_type: SourceType
    rights_status: RightsStatus
    source_reference_url: str | None = None
    style_guide_text: str | None = None


class ImportItemRequest(BaseModel):
    ordinal: int
    title: str | None = None
    text: str | None = None
    filename: str | None = None
    payload_base64: str | None = None


class ImportChaptersRequest(BaseModel):
    kind: ImportKind
    items: tuple[ImportItemRequest, ...]


class TransitionRequest(BaseModel):
    state: ChapterState


def create_projects_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/projects")
    active_settings = settings or Settings()

    def workflow_dependency() -> Iterator[ProjectWorkflow]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield ProjectWorkflow(session, ArtifactStore(active_settings.data_root))
        engine.dispose()

    @router.post("")
    def create_project(
        request: CreateProjectRequest,
        workflow: ProjectWorkflow = Depends(workflow_dependency),
    ) -> dict[str, object]:
        try:
            project = workflow.create_project(
                CreateProject(
                    request.title,
                    request.slug,
                    request.source_type,
                    request.rights_status,
                    request.source_reference_url,
                    request.style_guide_text,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _dataclass_dict(project)

    @router.post("/{project_id}/chapters/import")
    def import_chapters(
        project_id: str,
        request: ImportChaptersRequest,
        workflow: ProjectWorkflow = Depends(workflow_dependency),
    ) -> dict[str, object]:
        try:
            command = _import_command(request)
            chapters = workflow.import_chapters(project_id, command)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"chapters": [_dataclass_dict(chapter) for chapter in chapters]}

    @router.post("/chapters/{chapter_id}/transition")
    def transition_chapter(
        chapter_id: str,
        request: TransitionRequest,
        workflow: ProjectWorkflow = Depends(workflow_dependency),
    ) -> dict[str, object]:
        try:
            chapter = workflow.request_transition(chapter_id, request.state)
        except InvalidChapterTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _dataclass_dict(chapter)

    return router


def _import_command(request: ImportChaptersRequest) -> ImportChapters:
    if request.kind is ImportKind.PASTE:
        return ImportChapters(
            request.kind,
            tuple(
                ImportChapters.paste(item.ordinal, item.title, item.text or "").items[0]
                for item in request.items
            ),
        )
    if request.kind is ImportKind.TXT:
        import base64

        return ImportChapters(
            request.kind,
            tuple(
                ImportChapters.txt(
                    item.ordinal,
                    item.filename or f"chapter-{item.ordinal}.txt",
                    base64.b64decode(item.payload_base64 or "", validate=True),
                    item.title,
                ).items[0]
                for item in request.items
            ),
        )
    raise ValueError("IMPORT_KIND_UNSUPPORTED")


def _dataclass_dict(value: object) -> dict[str, object]:
    from dataclasses import asdict
    from enum import Enum

    def convert(item: object) -> object:
        if isinstance(item, Enum):
            return item.name
        if isinstance(item, Path):
            return item.as_posix()
        return item

    return {key: convert(item) for key, item in asdict(value).items()}
