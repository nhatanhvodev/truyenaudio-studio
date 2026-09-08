from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import cloud_profiles
from app.api.cloud_profiles import _contains_secret_key, _safe_config, create_cloud_profiles_router
from app.modules.security.credentials import CredentialStore, CredentialUnavailable
from app.providers.gemini_mt import GeminiMtAdapter
from app.providers.qwen_mt import Secret


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.values.pop((service_name, username), None)


class UnavailableKeyring:
    def get_password(self, service_name: str, username: str) -> str | None:
        raise RuntimeError("backend unavailable")

    def set_password(self, service_name: str, username: str, password: str) -> None:
        raise RuntimeError("backend unavailable")

    def delete_password(self, service_name: str, username: str) -> None:
        raise RuntimeError("password backend unavailable")


def test_credential_store_round_trip_uses_canonical_service_and_username() -> None:
    backend = FakeKeyring()
    store = CredentialStore(backend)

    ref = store.set("profile-1", "secret-value")

    assert ref == "keyring:truyenaudio-studio/provider-profile:profile-1"
    assert backend.values == {("truyenaudio-studio", "provider-profile:profile-1"): "secret-value"}
    handle = store.resolve("profile-1", ref)
    assert handle.value == "secret-value"
    assert repr(handle) == "SecretHandle(<redacted>)"


def test_credential_store_reads_legacy_ref_and_fails_closed() -> None:
    backend = FakeKeyring()
    backend.values[("legacy-service", "legacy-user")] = "legacy-secret"
    store = CredentialStore(backend)

    assert store.resolve("profile-1", "keyring:legacy-service/legacy-user").value == "legacy-secret"
    with pytest.raises(ValueError, match="SECRET_MISSING"):
        store.resolve("profile-1", "keyring:truyenaudio-studio/provider-profile:missing")
    with pytest.raises(ValueError, match="SECRET_REQUIRED"):
        store.set("profile-1", " ")


def test_credential_store_rotates_and_revokes_canonical_secret() -> None:
    backend = FakeKeyring()
    store = CredentialStore(backend)

    ref = store.set("profile-1", "first-secret")
    assert store.set("profile-1", "rotated-secret") == ref
    assert store.resolve("profile-1", ref).value == "rotated-secret"

    assert store.delete("profile-1", ref) is True
    with pytest.raises(ValueError, match="SECRET_MISSING"):
        store.resolve("profile-1", ref)


def test_qwen_and_gemini_resolve_the_same_canonical_keyring_ref() -> None:
    store = CredentialStore(FakeKeyring())
    ref = store.set("profile-1", "shared-api-key")

    assert Secret.from_ref(ref, credential_store=store).value == "shared-api-key"
    assert GeminiMtAdapter(api_key_ref=ref, credential_store=store).api_key == "shared-api-key"


def test_keyring_unavailable_fails_closed_for_all_credential_operations() -> None:
    store = CredentialStore(UnavailableKeyring())

    with pytest.raises(CredentialUnavailable, match="KEYRING_UNAVAILABLE"):
        store.set("profile-1", "secret")
    with pytest.raises(CredentialUnavailable, match="KEYRING_UNAVAILABLE"):
        store.resolve("profile-1")
    with pytest.raises(CredentialUnavailable, match="KEYRING_UNAVAILABLE"):
        store.delete("profile-1")


def test_profile_credential_lifecycle_hides_secret_and_secret_ref(settings, migrated_engine, monkeypatch: pytest.MonkeyPatch) -> None:
    store = CredentialStore(FakeKeyring())
    monkeypatch.setattr(cloud_profiles, "CredentialStore", lambda: store)
    app = FastAPI()
    app.include_router(create_cloud_profiles_router(settings))

    with TestClient(app) as client:
        created = client.post(
            "/api/cloud-profiles",
            json={
                "providerKind": "TRANSLATOR",
                "adapterName": "gemini_mt",
                "displayName": "Gemini",
                "enabled": True,
                "config": {"safe": True},
                "secret": "first-secret",
            },
        )
        assert created.status_code == 201
        profile_id = created.json()["id"]

        rotated = client.put(f"/api/cloud-profiles/{profile_id}/credential", json={"secret": "rotated-secret"})
        assert rotated.status_code == 200
        assert rotated.json() == {"secretConfigured": True}

        revoked = client.delete(f"/api/cloud-profiles/{profile_id}/credential")
        assert revoked.status_code == 200
        assert revoked.json() == {"secretConfigured": False}

        payloads = [created.json(), client.get("/api/cloud-profiles").json()]

    assert CredentialStore(FakeKeyring()).ref_for(profile_id) not in repr(payloads)
    assert "first-secret" not in repr(payloads)
    assert "rotated-secret" not in repr(payloads)
    assert _has_forbidden_profile_secret_field(payloads) is False


def _has_forbidden_profile_secret_field(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key.lower() in {"secret", "secret_ref", "secretref"}
            or _has_forbidden_profile_secret_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_profile_secret_field(item) for item in value)
    return False


def test_cloud_profile_config_rejects_nested_secret_keys_and_redacts_legacy_values() -> None:
    assert _contains_secret_key({"provider": {"apiKey": "secret"}})
    assert _contains_secret_key({"headers": [{"access_token": "secret"}]})
    assert _safe_config({"provider": "gemini", "apiKey": "secret", "nested": {"token": "secret", "x": 1}}) == {
        "provider": "gemini",
        "nested": {"x": 1},
    }
