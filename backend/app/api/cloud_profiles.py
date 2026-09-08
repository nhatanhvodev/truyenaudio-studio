from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.contracts import ProviderKind, new_id
from app.db.base import create_engine_for, session_factory
from app.db.models import ProviderProfile
from app.modules.security.credentials import CredentialStore, CredentialUnavailable
from app.settings.config import Settings


class ProviderProfileRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider_kind: ProviderKind = Field(alias="providerKind")
    adapter_name: str = Field(alias="adapterName")
    display_name: str = Field(alias="displayName")
    model: str | None = None
    region: str | None = None
    config: dict[str, object] = Field(default_factory=dict)
    enabled: bool = False
    secret: str | None = None


class ProviderProfilePatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    expected_revision: int = Field(alias="expectedRevision", ge=1)
    display_name: str | None = Field(default=None, alias="displayName")
    model: str | None = None
    region: str | None = None
    config: dict[str, object] | None = None
    enabled: bool | None = None


class CredentialRequest(BaseModel):
    secret: str = Field(min_length=1)


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

    @router.post("", status_code=status.HTTP_201_CREATED)
    def upsert_profile(
        request: ProviderProfileRequest,
        session=Depends(session_dependency),
    ) -> dict[str, object]:
        if _contains_secret_key(request.config):
            raise HTTPException(status_code=422, detail="SECRET_IN_CONFIG_FORBIDDEN")
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
        _commit_with_compensation(
            session,
            lambda: _delete_secret(profile_id, secret_write.secret_ref),
        )
        return _profile_payload(profile)

    @router.patch("/{profile_id}")
    def patch_profile(profile_id: str, request: ProviderProfilePatch, session=Depends(session_dependency)) -> dict[str, object]:
        profile = session.get(ProviderProfile, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="PROFILE_NOT_FOUND")
        if profile.revision != request.expected_revision:
            raise HTTPException(status_code=409, detail="PROFILE_REVISION_CONFLICT")
        if request.config is not None and _contains_secret_key(request.config):
            raise HTTPException(status_code=422, detail="SECRET_IN_CONFIG_FORBIDDEN")
        for field_name in ("display_name", "model", "region", "config", "enabled"):
            value = getattr(request, field_name)
            if value is not None:
                setattr(profile, "config_json" if field_name == "config" else field_name, value)
        profile.revision += 1
        session.commit()
        return _profile_payload(profile)

    @router.put("/{profile_id}/credential")
    def put_credential(profile_id: str, request: CredentialRequest, session=Depends(session_dependency)) -> dict[str, object]:
        profile = session.get(ProviderProfile, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="PROFILE_NOT_FOUND")
        store = CredentialStore()
        previous_ref = profile.secret_ref
        try:
            previous_secret = store.resolve(profile_id, previous_ref).value if previous_ref else None
        except CredentialUnavailable as exc:
            raise HTTPException(status_code=503, detail="KEYRING_UNAVAILABLE") from exc
        except ValueError as exc:
            if str(exc) != "SECRET_MISSING":
                raise HTTPException(status_code=503, detail="KEYRING_UNAVAILABLE") from exc
            previous_secret = None
        try:
            new_ref = store.set(profile_id, request.secret)
            profile.secret_ref = new_ref
        except Exception as exc:
            raise HTTPException(status_code=503, detail="KEYRING_UNAVAILABLE") from exc
        profile.revision += 1
        _commit_with_compensation(
            session,
            lambda: _restore_or_delete(store, profile_id, previous_ref, previous_secret, new_ref),
        )
        return {"secretConfigured": True}

    @router.delete("/{profile_id}/credential")
    def delete_credential(profile_id: str, session=Depends(session_dependency)) -> dict[str, object]:
        profile = session.get(ProviderProfile, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="PROFILE_NOT_FOUND")
        previous_ref = profile.secret_ref
        if previous_ref:
            try:
                store = CredentialStore()
                previous_secret = store.resolve(profile_id, previous_ref).value
                store.delete(profile_id, previous_ref)
            except Exception as exc:
                raise HTTPException(status_code=503, detail="KEYRING_UNAVAILABLE") from exc
        else:
            store = None
            previous_secret = None
        profile.secret_ref = None
        profile.revision += 1
        _commit_with_compensation(
            session,
            lambda: _restore_deleted_secret(store, profile_id, previous_ref, previous_secret),
        )
        return {"secretConfigured": False}

    @router.post("/{profile_id}/validate")
    def validate_credential(profile_id: str, session=Depends(session_dependency)) -> dict[str, object]:
        profile = session.get(ProviderProfile, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="PROFILE_NOT_FOUND")
        if not profile.secret_ref:
            return {"state": "invalid", "error": {"code": "CREDENTIAL_MISSING"}}
        try:
            CredentialStore().resolve(profile_id, profile.secret_ref)
        except Exception:
            return {"state": "unavailable", "error": {"code": "CREDENTIAL_UNAVAILABLE"}}
        return {"state": "ready", "error": None}

    return router


def _store_secret(profile_id: str, value: str | None) -> _SecretWrite:
    if value is None or value == "":
        return _SecretWrite(secret_ref=None)
    try:
        secret_ref = CredentialStore().set(profile_id, value)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="KEYRING_UNAVAILABLE") from exc
    return _SecretWrite(secret_ref=secret_ref)


def _commit(session) -> None:
    session.commit()


def _commit_with_compensation(session, compensate) -> None:
    try:
        _commit(session)
    except Exception as exc:
        session.rollback()
        try:
            compensate()
        except Exception as compensation_exc:
            raise HTTPException(status_code=503, detail="KEYRING_COMPENSATION_FAILED") from compensation_exc
        raise HTTPException(status_code=500, detail="PROFILE_PERSIST_FAILED") from exc


def _delete_secret(profile_id: str, secret_ref: str | None) -> None:
    if secret_ref:
        CredentialStore().delete(profile_id, secret_ref)


def _restore_or_delete(store, profile_id: str, previous_ref: str | None, previous_secret: str | None, new_ref: str | None) -> None:
    if previous_ref and previous_secret is not None:
        store.restore(profile_id, previous_ref, previous_secret)
    if new_ref and new_ref != previous_ref:
        store.delete(profile_id, new_ref)


def _restore_deleted_secret(store, profile_id: str, previous_ref: str | None, previous_secret: str | None) -> None:
    if store is not None and previous_ref and previous_secret is not None:
        store.restore(profile_id, previous_ref, previous_secret)


def _profile_payload(profile: ProviderProfile) -> dict[str, object]:
    return {
        "id": profile.id,
        "providerKind": profile.provider_kind,
        "adapterName": profile.adapter_name,
        "displayName": profile.display_name,
        "model": profile.model,
        "region": profile.region,
        "revision": profile.revision,
        "secretConfigured": bool(profile.secret_ref),
        "config": _safe_config(profile.config_json or {}),
        "enabled": profile.enabled,
        "status": "ready" if profile.enabled and profile.secret_ref else ("disabled" if not profile.enabled else "invalid"),
    }


_SECRET_KEY_PARTS = ("secret", "token", "password", "apikey", "api_key", "accesskey", "access_key")


def _contains_secret_key(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = "".join(character for character in str(key).lower() if character.isalnum())
            if any(part in normalized for part in _SECRET_KEY_PARTS):
                return True
            if _contains_secret_key(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_secret_key(item) for item in value)
    return False


def _safe_config(value: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in value.items():
        normalized = "".join(character for character in key.lower() if character.isalnum())
        if any(part in normalized for part in _SECRET_KEY_PARTS):
            continue
        result[key] = _safe_config_value(item)
    return result


def _safe_config_value(value: object) -> object:
    if isinstance(value, dict):
        return _safe_config(value)
    if isinstance(value, list):
        return [_safe_config_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_safe_config_value(item) for item in value)
    return value
