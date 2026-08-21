from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.contracts import ChapterState, ImportKind, RightsStatus, SourceType
from app.db.base import create_engine_for, session_factory
from app.modules.artifacts.store import ArtifactStore
from app.modules.projects.queries import ChapterQueries, InvalidCursor
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


def create_projects_router(settings: Settings | None = None, *, cursor_secret: str = "startup-token") -> APIRouter:
    router = APIRouter(prefix="/api/projects")
    active_settings = settings or Settings()

    def workflow_dependency() -> Iterator[ProjectWorkflow]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield ProjectWorkflow(session, ArtifactStore(active_settings.data_root))
        engine.dispose()

    def queries_dependency() -> Iterator[ChapterQueries]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        try:
            yield ChapterQueries(engine, cursor_secret=cursor_secret)
        finally:
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

    @router.get("/{project_id}/chapters")
    def list_chapters(
        project_id: str,
        limit: int = 25,
        cursor: str | None = None,
        queries: ChapterQueries = Depends(queries_dependency),
    ) -> dict[str, object]:
        try:
            page = queries.list_chapters(project_id, limit=limit, cursor=cursor)
        except InvalidCursor as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _camelize(_dataclass_dict(page))

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
    from dataclasses import asdict, is_dataclass
    from enum import Enum

    def convert(item: object) -> object:
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, Path):
            return item.as_posix()
        if is_dataclass(item) and not isinstance(item, type):
            return {key: convert(value) for key, value in asdict(item).items()}
        if isinstance(item, tuple | list):
            return [convert(value) for value in item]
        if isinstance(item, dict):
            return {key: convert(value) for key, value in item.items()}
        return item

    return {key: convert(item) for key, item in asdict(value).items()}


def _camelize(value: object) -> object:
    if isinstance(value, dict):
        return {_camel_key(key): _camelize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


def _camel_key(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)
