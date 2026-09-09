"""Shared normalized classifiers for configuration and diagnostics redaction."""

from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import unquote


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
    for candidate in _decoded_candidates(str(key)):
        normalized = "".join(character for character in candidate.lower() if character.isalnum())
        if any(part in normalized for part in _SECRET_KEY_PARTS):
            return True
    return False


def _decoded_candidates(value: str) -> Iterator[str]:
    candidate = value
    yield candidate
    for _round in range(6):
        decoded = unquote(candidate)
        if decoded == candidate:
            return
        candidate = decoded
        yield candidate
