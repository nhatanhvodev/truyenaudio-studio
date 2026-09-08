"""Validation for model identifiers used in provider configuration and URLs."""

from __future__ import annotations

import re


MODEL_IDENTIFIER_INVALID = "MODEL_IDENTIFIER_INVALID"
_MODEL_IDENTIFIER = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*",
    re.ASCII,
)
_MAX_MODEL_IDENTIFIER_LENGTH = 255


def validate_model_identifier(value: object) -> str:
    """Return a URL-path-safe provider model identifier or raise a fixed error.

    Model identifiers may use slash-delimited vendor namespaces, but each path
    segment must begin with an alphanumeric character. URL delimiters, escapes,
    backslashes, whitespace, and dot-path traversal are not model syntax.
    """
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_MODEL_IDENTIFIER_LENGTH
        or _MODEL_IDENTIFIER.fullmatch(value) is None
    ):
        raise ValueError(MODEL_IDENTIFIER_INVALID)
    return value
