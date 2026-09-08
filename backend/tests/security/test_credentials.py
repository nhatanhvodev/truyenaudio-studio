from __future__ import annotations

import pytest

from app.modules.security.credentials import CredentialStore
from app.api.cloud_profiles import _contains_secret_key, _safe_config


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.values.pop((service_name, username), None)


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


def test_cloud_profile_config_rejects_nested_secret_keys_and_redacts_legacy_values() -> None:
    assert _contains_secret_key({"provider": {"apiKey": "secret"}})
    assert _contains_secret_key({"headers": [{"access_token": "secret"}]})
    assert _safe_config({"provider": "gemini", "apiKey": "secret", "nested": {"token": "secret", "x": 1}}) == {
        "provider": "gemini",
        "nested": {"x": 1},
    }
