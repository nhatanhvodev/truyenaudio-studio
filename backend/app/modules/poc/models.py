from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import hashlib
import json


HEX64_LENGTH = 64


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: tuple[str, ...]
    metrics: dict[str, int | float | str | bool]

    def to_canonical_json(self) -> str:
        return canonical_json(self)

    def sha256(self) -> str:
        return hashlib.sha256(self.to_canonical_json().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PocTranslationScore:
    critical: int
    major: int
    minor: int
    latency_ms: int
    cost_vnd: int
    major_dispositions: tuple[str, ...] = ()
    provider: str = ""
    model: str = ""
    provider_version: str = ""
    usage_units: int = 0
    model_snapshot_hash: str = ""
    evidence_artifact_sha256: str = ""


@dataclass(frozen=True)
class VoiceEvidence:
    provider: str
    voice_id: str
    model: str
    provider_version: str
    model_snapshot_hash: str
    license_artifact_sha256: str


@dataclass(frozen=True)
class PocTtsScore:
    pronunciation_1_5: float
    continuity_1_5: float
    rtf: float
    peak_working_set_mb: int
    duration_minutes: float
    voices: tuple[VoiceEvidence, ...] = ()
    critical_lost_repeated_content: int = 0
    oom: bool = False
    concurrency: int = 1


def canonical_json(value: object) -> str:
    return json.dumps(_to_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _to_jsonable(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    return value


def is_sha256(value: str) -> bool:
    return len(value) == HEX64_LENGTH and all(character in "0123456789abcdef" for character in value)
