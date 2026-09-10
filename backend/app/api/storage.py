from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, is_dataclass
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.db.base import create_engine_for, session_factory
from app.modules.artifacts.store import ArtifactStore
from app.modules.storage.backup import (
    BackupError,
    BackupProgressTracker,
    BackupService,
    BackupVerificationError,
    RestoreConfirmationRequired,
    RestoreLockRequired,
    RetentionCountInvalid,
)
from app.modules.storage.cleanup import CleanupPlan, CleanupPlanStale, CleanupService
from app.modules.storage.disk import DiskGuard
from app.settings.config import Settings
from app.settings.startup_lock import AlreadyRunning


class CleanupExecuteRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    plan_id: str = Field(alias="planId")
    snapshot_hash: str = Field(alias="snapshotHash")


class RetentionRequest(BaseModel):
    count: int


class RestoreCopyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    target_data_root: str = Field(alias="targetDataRoot")
    confirm_target: str | None = Field(default=None, alias="confirmTarget")


def create_storage_router(
    settings: Settings | None = None,
    *,
    progress_tracker: BackupProgressTracker | None = None,
) -> APIRouter:
    """Storage routes; ``progress_tracker`` lets a caller observe the running backup.

    A dedicated tracker is created when none is given, so the progress route below always
    answers for this router instance.
    """
    router = APIRouter(prefix="/api/storage")
    active_settings = settings or Settings()
    cleanup_plans: dict[str, CleanupPlan] = {}
    tracker = progress_tracker or BackupProgressTracker()

    def backup_service() -> BackupService:
        return BackupService(
            db_path=active_settings.data_root / "studio.sqlite3",
            backup_root=active_settings.data_root / "backups",
            artifact_root=active_settings.data_root,
        )

    def cleanup_service() -> Iterator[CleanupService]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        try:
            with factory() as session:
                yield CleanupService(
                    session,
                    ArtifactStore(active_settings.data_root),
                    plan_store=cleanup_plans,
                )
        finally:
            engine.dispose()

    @router.get("/disk")
    def disk(estimated_bytes: int = Query(default=0, alias="estimatedBytes")) -> dict[str, object]:
        return _camel_payload(DiskGuard(active_settings.data_root).can_create(estimated_bytes))

    @router.post("/backups")
    def create_backup(service: BackupService = Depends(backup_service)) -> dict[str, object]:
        """Run one backup and report its real milestones to GET /backups/progress."""
        tracker.begin()
        try:
            return _camel_payload(service.create(progress=tracker))
        except BackupVerificationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            tracker.end()

    @router.get(
        "/backups/progress",
        responses={204: {"description": "No backup is running right now"}},
    )
    def backup_progress() -> Response:
        """Progress of the create() running right now, or 204 when none is in flight."""
        progress = tracker.snapshot()
        if progress is None:
            return Response(status_code=204)
        return JSONResponse(_camel_payload(progress))

    @router.get("/backups")
    def list_backups(service: BackupService = Depends(backup_service)) -> dict[str, object]:
        return {"backups": _camel_payload(list(service.list_backups()))}

    @router.post("/backups/{backup_id}/restore")
    def restore_backup(backup_id: str, service: BackupService = Depends(backup_service)) -> dict[str, object]:
        try:
            with service.acquire_restore_locks() as token:
                return _camel_payload(service.restore_to(backup_id, lock_token=token))
        except (AlreadyRunning, RestoreLockRequired) as exc:
            raise HTTPException(status_code=409, detail="RESTORE_REQUIRES_STOPPED_API_AND_WORKER") from exc
        except BackupVerificationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/backups/{backup_id}/restore-copy")
    def restore_backup_copy(
        backup_id: str,
        request: RestoreCopyRequest,
        service: BackupService = Depends(backup_service),
    ) -> dict[str, object]:
        """Restore a verified backup into a new isolated data root (never in place)."""
        try:
            with service.acquire_restore_locks() as token:
                return _camel_payload(
                    service.restore_copy(
                        backup_id,
                        request.target_data_root,
                        confirm_target=request.confirm_target,
                        lock_token=token,
                    )
                )
        except RestoreConfirmationRequired as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (AlreadyRunning, RestoreLockRequired) as exc:
            raise HTTPException(status_code=409, detail="RESTORE_REQUIRES_STOPPED_API_AND_WORKER") from exc
        except BackupVerificationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BackupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/retention")
    def read_retention(
        count: int = Query(alias="count"),
        service: BackupService = Depends(backup_service),
    ) -> dict[str, object]:
        try:
            return _camel_payload(service.retention_plan(count))
        except RetentionCountInvalid as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/retention")
    def write_retention(
        request: RetentionRequest,
        service: BackupService = Depends(backup_service),
    ) -> dict[str, object]:
        """Validate a retention change and return its plan — **nothing is deleted here**."""
        try:
            return _camel_payload(service.retention_plan(request.count))
        except RetentionCountInvalid as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/cleanup/preview")
    def cleanup_preview(service: CleanupService = Depends(cleanup_service)) -> dict[str, object]:
        return _camel_payload(service.preview())

    @router.post("/cleanup/execute")
    def cleanup_execute(
        request: CleanupExecuteRequest,
        service: CleanupService = Depends(cleanup_service),
    ) -> dict[str, object]:
        try:
            return _camel_payload(service.execute(request.plan_id, request.snapshot_hash))
        except CleanupPlanStale as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router


def _camel_payload(value: object) -> dict[str, object]:
    return _camelize(_convert(value))


def _convert(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _convert(item) for key, item in asdict(value).items()}
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
