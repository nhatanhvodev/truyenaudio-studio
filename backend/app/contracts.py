from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from uuid6 import uuid7


def new_id() -> str:
    return str(uuid7())


class SourceType(StrEnum):
    SELF_AUTHORED = "SELF_AUTHORED"
    PUBLIC_DOMAIN = "PUBLIC_DOMAIN"
    OPEN_LICENSE = "OPEN_LICENSE"
    LICENSED_PARTNER = "LICENSED_PARTNER"
    USER_SUPPLIED_PRIVATE = "USER_SUPPLIED_PRIVATE"
    UNKNOWN = "UNKNOWN"


class ImportKind(StrEnum):
    PASTE = "PASTE"
    TXT = "TXT"
    EPUB = "EPUB"
    DOCX = "DOCX"
    LOCAL_FOLDER = "LOCAL_FOLDER"
    OFFICIAL_YUEWEN_API = "OFFICIAL_YUEWEN_API"


class RightsStatus(StrEnum):
    PRIVATE_ONLY = "PRIVATE_ONLY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    CLEARED = "CLEARED"
    EXPIRED = "EXPIRED"
    BLOCKED = "BLOCKED"


class RightsScope(StrEnum):
    TRANSLATE_VI = "TRANSLATE_VI"
    CREATE_AUDIO = "CREATE_AUDIO"
    PUBLIC_STREAM = "PUBLIC_STREAM"
    DOWNLOAD = "DOWNLOAD"
    MONETIZE = "MONETIZE"


class EvidenceKind(StrEnum):
    CONTRACT = "CONTRACT"
    LICENSE = "LICENSE"
    AUTHOR_PERMISSION = "AUTHOR_PERMISSION"
    PUBLIC_DOMAIN_PROOF = "PUBLIC_DOMAIN_PROOF"
    OPEN_LICENSE_SNAPSHOT = "OPEN_LICENSE_SNAPSHOT"
    USER_ATTESTATION = "USER_ATTESTATION"
    VOICE_CONSENT = "VOICE_CONSENT"
    MODEL_LICENSE_SNAPSHOT = "MODEL_LICENSE_SNAPSHOT"


class CloudConsentStatus(StrEnum):
    NOT_GRANTED = "NOT_GRANTED"
    GRANTED = "GRANTED"
    REVOKED = "REVOKED"


class ChapterState(StrEnum):
    IMPORTED = "IMPORTED"
    NORMALIZED = "NORMALIZED"
    TRANSLATING = "TRANSLATING"
    TRANSLATION_REVIEW = "TRANSLATION_REVIEW"
    TRANSLATION_APPROVED = "TRANSLATION_APPROVED"
    VOICE_CONFIGURED = "VOICE_CONFIGURED"
    TTS_QUEUED = "TTS_QUEUED"
    SYNTHESIZING = "SYNTHESIZING"
    AUDIO_REVIEW = "AUDIO_REVIEW"
    READY_TO_EXPORT = "READY_TO_EXPORT"
    EXPORTED = "EXPORTED"
    FAILED = "FAILED"


class JobKind(StrEnum):
    IMPORT = "IMPORT"
    TRANSLATE = "TRANSLATE"
    REVIEW = "REVIEW"
    REPAIR_TRANSLATION = "REPAIR_TRANSLATION"
    PREVIEW_TTS = "PREVIEW_TTS"
    SYNTHESIZE = "SYNTHESIZE"
    MASTER = "MASTER"
    AUDIO_QA = "AUDIO_QA"
    EXPORT = "EXPORT"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    BLOCKED_BUDGET = "BLOCKED_BUDGET"
    BILLING_UNKNOWN = "BILLING_UNKNOWN"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"


class ProviderKind(StrEnum):
    TRANSLATOR = "TRANSLATOR"
    REVIEWER = "REVIEWER"
    TTS = "TTS"
    ASR = "ASR"


class QaCategory(StrEnum):
    COMPLETENESS = "COMPLETENESS"
    ACCURACY = "ACCURACY"
    TERMINOLOGY = "TERMINOLOGY"
    NAME = "NAME"
    PRONOUN_GENDER = "PRONOUN_GENDER"
    NUMBER_UNIT = "NUMBER_UNIT"
    RESIDUAL_HAN = "RESIDUAL_HAN"
    REPETITION = "REPETITION"
    META_TEXT = "META_TEXT"
    STYLE = "STYLE"
    TTS_LENGTH = "TTS_LENGTH"
    AUDIO_TECHNICAL = "AUDIO_TECHNICAL"
    PRONUNCIATION = "PRONUNCIATION"
    SILENCE = "SILENCE"
    CLIPPING = "CLIPPING"


class QaSeverity(StrEnum):
    INFO = "INFO"
    MINOR = "MINOR"
    MAJOR = "MAJOR"
    CRITICAL = "CRITICAL"


class QaStatus(StrEnum):
    OPEN = "OPEN"
    ACCEPTED_RISK = "ACCEPTED_RISK"
    FIXED = "FIXED"
    DISMISSED = "DISMISSED"


class VoiceMode(StrEnum):
    SINGLE_NARRATOR = "SINGLE_NARRATOR"
    ASSISTED_MULTI_VOICE = "ASSISTED_MULTI_VOICE"


class VoiceOrigin(StrEnum):
    BUILT_IN = "BUILT_IN"
    CLOUD_CATALOG = "CLOUD_CATALOG"
    USER_REFERENCE = "USER_REFERENCE"


class ArtifactKind(StrEnum):
    SOURCE_SNAPSHOT = "SOURCE_SNAPSHOT"
    TRANSLATION_MARKDOWN = "TRANSLATION_MARKDOWN"
    TTS_SEGMENT = "TTS_SEGMENT"
    VOICE_PREVIEW = "VOICE_PREVIEW"
    MASTER_WAV = "MASTER_WAV"
    MASTER_MP3 = "MASTER_MP3"
    SRT = "SRT"
    REPORT = "REPORT"
    RIGHTS_EVIDENCE = "RIGHTS_EVIDENCE"
    LICENSE_SNAPSHOT = "LICENSE_SNAPSHOT"
    CONSENT_EVIDENCE = "CONSENT_EVIDENCE"
    PRIVATE_ARCHIVE = "PRIVATE_ARCHIVE"
    PUBLICATION_BUNDLE = "PUBLICATION_BUNDLE"


class ArtifactStatus(StrEnum):
    WRITING = "WRITING"
    READY = "READY"
    CORRUPT = "CORRUPT"
    SUPERSEDED = "SUPERSEDED"
    DELETED = "DELETED"


class ExportKind(StrEnum):
    PRIVATE_ARCHIVE = "PRIVATE_ARCHIVE"
    PUBLICATION_BUNDLE = "PUBLICATION_BUNDLE"


class ExportStatus(StrEnum):
    BUILDING = "BUILDING"
    READY = "READY"
    FAILED = "FAILED"
    REVOKED = "REVOKED"


class UsageUnit(StrEnum):
    INPUT_TOKEN = "INPUT_TOKEN"
    OUTPUT_TOKEN = "OUTPUT_TOKEN"
    CHARACTER = "CHARACTER"
    UTF8_BYTE = "UTF8_BYTE"
    AUDIO_TOKEN = "AUDIO_TOKEN"
    AUDIO_SECOND = "AUDIO_SECOND"
    REQUEST = "REQUEST"


@dataclass(frozen=True)
class Usage:
    unit: str
    measured_units: int
    provider_request_id: str | None = None


@dataclass(frozen=True)
class OperationContext:
    operation_id: str
    cache_key: str
    timeout_seconds: int
    estimated_units: int
    budget_authorization_id: str | None
    cloud_consent_id: str | None
    billing_category: str = "REGULAR"


@dataclass(frozen=True)
class TranslationRequest:
    context: OperationContext
    source_segment_id: str
    source_text: str
    source_language: str
    target_language: str
    terms: tuple[tuple[str, str], ...]
    tm_list: tuple[tuple[str, str], ...]
    domain_instruction: str
    story_memory: tuple[str, ...]


@dataclass(frozen=True)
class TranslationResult:
    target_text: str
    provider: str
    model: str
    provider_version: str
    usage: tuple[Usage, ...]


@dataclass(frozen=True)
class ReviewRequest:
    context: OperationContext
    source_segment_id: str
    source_text: str
    target_text: str


@dataclass(frozen=True)
class ReviewFinding:
    source_segment_id: str
    category: str
    severity: str
    evidence: str
    suggestion: str


@dataclass(frozen=True)
class ReviewResult:
    findings: tuple[ReviewFinding, ...]
    provider: str
    model: str
    provider_version: str
    usage: tuple[Usage, ...]


@dataclass(frozen=True)
class SynthesisRequest:
    context: OperationContext
    speech_segment_id: str
    narration_text: str
    locale: str
    voice_id: str
    speed: str
    pitch: str
    style: str | None
    sample_rate: int


@dataclass(frozen=True)
class SynthesisResult:
    provider: str
    model: str
    provider_version: str
    duration_ms: int
    sha256: str
    usage: tuple[Usage, ...]


@dataclass(frozen=True)
class MasterRequest:
    operation_id: str
    ordered_segment_paths: tuple[Path, ...]
    pause_after_ms: tuple[int, ...]
    metadata: dict[str, str]
    sample_rate: int = 44_100
    integrated_lufs: float = -16.0
    true_peak_dbtp: float = -1.5
    loudness_range: float = 11.0


@dataclass(frozen=True)
class MasterResult:
    duration_ms: int
    sha256: str
    codec: str
    sample_rate: int
    channels: int
    bitrate_kbps: int
    integrated_lufs: float
    true_peak_dbtp: float


class TranslatorAdapter(Protocol):
    def capabilities(self) -> dict[str, object]: ...
    async def translate(self, request: TranslationRequest) -> TranslationResult: ...


class ReviewerAdapter(Protocol):
    async def review(self, request: ReviewRequest) -> ReviewResult: ...


class TtsAdapter(Protocol):
    def capabilities(self) -> dict[str, object]: ...
    async def list_voices(self, locale: str) -> list[dict[str, object]]: ...
    async def synthesize(self, request: SynthesisRequest, output_path: Path) -> SynthesisResult: ...


class AudioProcessor(Protocol):
    async def master(self, request: MasterRequest, output_path: Path) -> MasterResult: ...
    async def probe(self, path: Path) -> MasterResult: ...


class JobRunner(Protocol):
    def enqueue(self, *args: object, **kwargs: object) -> object: ...
    def claim(self, *args: object, **kwargs: object) -> object: ...
    def heartbeat(self, job_id: str, worker_id: str, attempt_id: str, now: datetime) -> object: ...
    def request_cancel(self, *args: object, **kwargs: object) -> object: ...
    def acknowledge_cancel(self, *args: object, **kwargs: object) -> object: ...
    def complete(self, *args: object, **kwargs: object) -> object: ...
    def fail(self, *args: object, **kwargs: object) -> object: ...


class BudgetGuard(Protocol):
    def quote(self, *args: object, **kwargs: object) -> object: ...
    def authorize(self, *args: object, **kwargs: object) -> object: ...
    def commit_usage(self, *args: object, **kwargs: object) -> object: ...
    def release_authorization(self, *args: object, **kwargs: object) -> object: ...


class ProjectWorkflow(Protocol):
    def create_project(self, *args: object, **kwargs: object) -> object: ...
    def import_chapters(self, *args: object, **kwargs: object) -> object: ...
    def request_transition(self, *args: object, **kwargs: object) -> object: ...
    def get_workspace(self, *args: object, **kwargs: object) -> object: ...


class TranslationWorkflow(Protocol):
    def estimate(self, *args: object, **kwargs: object) -> object: ...
    def enqueue_translation(self, *args: object, **kwargs: object) -> object: ...
    def approve_revision(self, *args: object, **kwargs: object) -> object: ...
    def revise_segment(self, *args: object, **kwargs: object) -> object: ...


class SpeechWorkflow(Protocol):
    def preview(self, *args: object, **kwargs: object) -> object: ...
    def enqueue_render(self, *args: object, **kwargs: object) -> object: ...
    def regenerate_segments(self, *args: object, **kwargs: object) -> object: ...
    def approve_audio(self, *args: object, **kwargs: object) -> object: ...


class ExportWorkflow(Protocol):
    def evaluate_gate(self, *args: object, **kwargs: object) -> object: ...
    def build_private_archive(self, *args: object, **kwargs: object) -> object: ...
    def build_publication_bundle(self, *args: object, **kwargs: object) -> object: ...
    def verify_bundle(self, *args: object, **kwargs: object) -> object: ...
