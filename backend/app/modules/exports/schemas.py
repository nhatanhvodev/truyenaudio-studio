from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


EXPORT_SCHEMA_VERSION = "truyenaudio-studio.export.v1"


@dataclass(frozen=True)
class PublicationMetadata:
    episode_title: str
    suggested_episode_number: int
    is_premium: bool

    def __post_init__(self) -> None:
        if not self.episode_title.strip():
            raise ValueError("EPISODE_TITLE_REQUIRED")
        if self.suggested_episode_number < 1:
            raise ValueError("EPISODE_NUMBER_POSITIVE_REQUIRED")
        if type(self.is_premium) is not bool:
            raise ValueError("IS_PREMIUM_BOOLEAN_REQUIRED")


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reasons: tuple[str, ...]
    rights_evaluation_hash: str


@dataclass(frozen=True)
class ExportBundle:
    id: str
    chapter_id: str
    kind: str
    status: str
    files: tuple[str, ...]
    directory_path: Path
    zip_path: Path
    artifact_id: str
    manifest_sha256: str
