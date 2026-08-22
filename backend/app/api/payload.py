from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum


def camel_payload(value: object) -> dict[str, object]:
    source = asdict(value) if is_dataclass(value) else value
    converted = _camelize(_convert(source))
    if not isinstance(converted, dict):
        raise TypeError("payload root must be a mapping")
    return converted


def _convert(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
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
    parts = value.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])
