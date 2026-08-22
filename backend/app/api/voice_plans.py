from __future__ import annotations

from collections.abc import Iterator
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.payload import camel_payload
from app.db.base import create_engine_for, session_factory
from app.modules.voices.roles import AssistedVoicePlanService, RoleSpec, VoicePlanConflict, VoicePlanInvalid
from app.settings.config import Settings


class RoleRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role_key: str = Field(alias="roleKey")
    display_name: str = Field(alias="displayName")
    voice_preset_id: str = Field(alias="voicePresetId")


class CreateMultiVoicePlanRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    chapter_id: str = Field(alias="chapterId")
    narrator_preset_id: str = Field(alias="narratorPresetId")
    roles: tuple[RoleRequest, ...] = ()


class AssignRolesRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_hash: str = Field(alias="expectedHash")
    assignments: dict[str, str]


def create_voice_plans_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/voice-plans")
    active_settings = settings or Settings()

    def service_dependency() -> Iterator[AssistedVoicePlanService]:
        db_path = active_settings.data_root / "studio.sqlite3"
        engine = create_engine_for(db_path)
        factory = session_factory(engine)
        try:
            with factory() as session:
                yield AssistedVoicePlanService(session)
        finally:
            engine.dispose()

    @router.post("/multi")
    def create_multi_voice_plan(
        request: CreateMultiVoicePlanRequest,
        service: AssistedVoicePlanService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            plan = service.create_multi(
                request.chapter_id,
                narrator_preset_id=request.narrator_preset_id,
                roles=tuple(
                    RoleSpec(role.role_key, role.display_name, role.voice_preset_id)
                    for role in request.roles
                ),
            )
            return _payload(plan)
        except VoicePlanInvalid as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/{plan_id}/assignments")
    def assign_roles(
        plan_id: str,
        request: AssignRolesRequest,
        service: AssistedVoicePlanService = Depends(service_dependency),
    ) -> dict[str, object]:
        try:
            return _payload(
                service.assign_roles(
                    plan_id,
                    expected_hash=request.expected_hash,
                    assignments=request.assignments,
                )
            )
        except VoicePlanConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except VoicePlanInvalid as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router


def _payload(value: object) -> dict[str, object]:
    return camel_payload(value)
