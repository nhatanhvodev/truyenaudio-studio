"""Shared normalized classifiers for configuration and diagnostics redaction."""

from __future__ import annotations


_SECRET_KEY_PARTS = (
    "secret",
    "token",
    "password",
    "apikey",
    "accesskey",
    "privatekey",
    "authorization",
    "credential",
    "bearer",
)


def is_secret_key(key: object) -> bool:
    """Return whether a normalized mapping key can carry a credential."""
    normalized = "".join(character for character in str(key).lower() if character.isalnum())
    return any(part in normalized for part in _SECRET_KEY_PARTS)
