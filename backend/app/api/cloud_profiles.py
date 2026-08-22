from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.contracts import ProviderKind, new_id
from app.db.base import create_engine_for, session_factory
from app.db.models import ProviderProfile
from app.settings.config import Settings


KEYRING_SERVICE = "truyenaudio-studio"


class ProviderProfileRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider_kind: ProviderKind = Field(alias="providerKind")
    adapter_name: str = Field(alias="adapterName")
    display_name: str = Field(alias="displayName")
    model: str | None = None
    region: str | None = None
    config: dict[str, object] = {}
    enabled: bool = False
    secret: str | None = None


@dataclass(frozen=True)
class _SecretWrite:
    secret_ref: str | None


def create_cloud_profiles_router(settings: Settings | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/cloud-profiles")
    active_settings = settings or Settings()

    def session_dependency() -> Iterator[object]:
        engine = create_engine_for(active_settings.data_root / "studio.sqlite3")
        factory = session_factory(engine)
        try:
            with factory() as session:
                yield session
        finally:
            engine.dispose()

    @router.get("")
    def list_profiles(session=Depends(session_dependency)) -> dict[str, object]:
        rows = session.scalars(select(ProviderProfile).order_by(ProviderProfile.display_name)).all()
        return {"profiles": [_profile_payload(row) for row in rows]}

    @router.post("")
    def upsert_profile(
        request: ProviderProfileRequest,
        session=Depends(session_dependency),
    ) -> dict[str, object]:
        profile_id = new_id()
        secret_write = _store_secret(profile_id, request.secret)
        profile = ProviderProfile(
            id=profile_id,
            provider_kind=request.provider_kind.value,
            adapter_name=request.adapter_name,
            display_name=request.display_name,
            model=request.model,
            region=request.region,
            secret_ref=secret_write.secret_ref,
            config_json=request.config,
            enabled=request.enabled,
        )
        session.add(profile)
        session.commit()
        return _profile_payload(profile)

    return router


def _store_secret(profile_id: str, value: str | None) -> _SecretWrite:
    if value is None or value == "":
        return _SecretWrite(secret_ref=None)
    secret_ref = f"provider-profile:{profile_id}"
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, secret_ref, value)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="KEYRING_UNAVAILABLE") from exc
    return _SecretWrite(secret_ref=f"keyring:{secret_ref}")


def _profile_payload(profile: ProviderProfile) -> dict[str, object]:
    return {
        "id": profile.id,
        "providerKind": profile.provider_kind,
        "adapterName": profile.adapter_name,
        "displayName": profile.display_name,
        "model": profile.model,
        "region": profile.region,
        "secretConfigured": bool(profile.secret_ref),
        "config": profile.config_json or {},
        "enabled": profile.enabled,
    }
