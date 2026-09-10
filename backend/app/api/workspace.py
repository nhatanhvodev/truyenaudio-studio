from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.base import create_engine_for, session_factory
from app.modules.workspace.layout import LayoutConflict, load_layout, save_layout
from app.settings.config import Settings


class LayoutSaveRequest(BaseModel):
    layout: dict[str, object]
    expected_revision: int | None = None


def create_workspace_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter()
    active_settings = settings or Settings()

    def session_dependency() -> Iterator[object]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        with factory() as session:
            yield session
        engine.dispose()

    @router.get("/api/projects/{project_id}/workspace-layout")
    def read_layout(
        project_id: str,
        session: object = Depends(session_dependency),
    ) -> dict[str, object]:
        try:
            view = load_layout(session, project_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "projectId": view.project_id,
            "layout": view.layout,
            "layoutVersion": view.layout_version,
            "revision": view.revision,
            "migrated": view.migrated,
        }

    @router.put("/api/projects/{project_id}/workspace-layout")
    def write_layout(
        project_id: str,
        request: LayoutSaveRequest,
        session: object = Depends(session_dependency),
    ) -> dict[str, object]:
        try:
            view = save_layout(
                session,
                project_id,
                request.layout,
                expected_revision=request.expected_revision,
            )
        except LayoutConflict as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "projectId": view.project_id,
            "layout": view.layout,
            "layoutVersion": view.layout_version,
            "revision": view.revision,
            "migrated": view.migrated,
        }

    return router
