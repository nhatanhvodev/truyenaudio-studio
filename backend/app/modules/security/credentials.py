"""Backend-only credential storage with canonical and legacy keyring readers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


KEYRING_SERVICE = "truyenaudio-studio"


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class CredentialUnavailable(RuntimeError):
    """The keyring could not be reached, so secret access must stop."""


@dataclass(frozen=True)
class SecretHandle:
    """Opaque secret value that intentionally cannot be serialized in repr/logs."""

    _value: str

    @property
    def value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretHandle(<redacted>)"


class CredentialStore:
    def __init__(self, backend: KeyringBackend | None = None) -> None:
        if backend is None:
            try:
                import keyring
            except ImportError as exc:  # pragma: no cover - dependency is pinned
                raise RuntimeError("KEYRING_UNAVAILABLE") from exc
            backend = keyring
        self._backend = backend

    @staticmethod
    def username(profile_id: str) -> str:
        if not profile_id.strip():
            raise ValueError("PROFILE_ID_REQUIRED")
        return f"provider-profile:{profile_id}"

    @classmethod
    def ref_for(cls, profile_id: str) -> str:
        return f"keyring:{KEYRING_SERVICE}/{cls.username(profile_id)}"

    def set(self, profile_id: str, secret: str) -> str:
        if not isinstance(secret, str) or not secret.strip():
            raise ValueError("SECRET_REQUIRED")
        username = self.username(profile_id)
        try:
            self._backend.set_password(KEYRING_SERVICE, username, secret)
        except Exception as exc:
            raise CredentialUnavailable("KEYRING_UNAVAILABLE") from exc
        return self.ref_for(profile_id)

    def resolve(self, profile_id: str, secret_ref: str | None = None) -> SecretHandle:
        """Resolve canonical refs and read legacy ``keyring:service/user`` refs."""
        service, username = self._location(profile_id, secret_ref)
        try:
            value = self._backend.get_password(service, username)
        except Exception as exc:
            raise CredentialUnavailable("KEYRING_UNAVAILABLE") from exc
        if not value:
            raise ValueError("SECRET_MISSING")
        return SecretHandle(value)

    def delete(self, profile_id: str, secret_ref: str | None = None) -> bool:
        service, username = self._location(profile_id, secret_ref)
        try:
            self._backend.delete_password(service, username)
        except Exception as exc:
            # The keyring package consistently uses PasswordDeleteError for a
            # missing entry.  Do not infer absence from arbitrary backend text:
            # that would turn an outage into a successful revoke.
            if type(exc).__name__ == "PasswordDeleteError":
                return False
            raise CredentialUnavailable("KEYRING_UNAVAILABLE") from exc
        return True

    def restore(self, profile_id: str, secret_ref: str, secret: str) -> None:
        """Restore a transient secret after its database mutation is rolled back."""
        if not isinstance(secret, str) or not secret:
            raise ValueError("SECRET_REQUIRED")
        service, username = self._location(profile_id, secret_ref)
        try:
            self._backend.set_password(service, username, secret)
        except Exception as exc:
            raise CredentialUnavailable("KEYRING_UNAVAILABLE") from exc

    def _location(self, profile_id: str, secret_ref: str | None) -> tuple[str, str]:
        ref = (secret_ref or self.ref_for(profile_id)).strip()
        if not ref.startswith("keyring:"):
            raise ValueError("SECRET_REF_UNSUPPORTED")
        raw = ref.removeprefix("keyring:")
        if "/" in raw:
            service, username = raw.split("/", 1)
        else:
            service, username = KEYRING_SERVICE, raw
        return service, username
