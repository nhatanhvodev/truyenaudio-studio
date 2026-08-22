from __future__ import annotations

from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ChapterState,
    CloudConsentStatus,
    EvidenceKind,
    ExportKind,
    ExportStatus,
    ImportKind,
    JobKind,
    JobStatus,
    ProviderKind,
    QaCategory,
    QaSeverity,
    QaStatus,
    RightsScope,
    RightsStatus,
    RunStatus,
    SourceType,
    UsageUnit,
    VoiceMode,
    VoiceOrigin,
)
from app.db.base import Base, TZDateTime, utc_now


UUID = String(36)


def enum_constraint(column_name: str, enum_type: type[StrEnum]) -> CheckConstraint:
    allowed = ", ".join(f"'{member.value}'" for member in enum_type)
    return CheckConstraint(f"{column_name} IN ({allowed})", name=f"{column_name}_enum")


def hash_constraint(column_name: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column_name} IS NULL OR (length({column_name}) = 64 AND {column_name} NOT GLOB '*[^0-9a-f]*')",
        name=f"{column_name}_lowercase_sha256",
    )


class MutableMixin:
    created_at: Mapped[object] = mapped_column(TZDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[object] = mapped_column(TZDateTime, default=utc_now, onupdate=utc_now, nullable=False)


class CreatedAtMixin:
    created_at: Mapped[object] = mapped_column(TZDateTime, default=utc_now, nullable=False)


class Project(MutableMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_projects_slug"),
        enum_constraint("source_type", SourceType),
        enum_constraint("rights_status", RightsStatus),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    original_title: Mapped[str | None] = mapped_column(Text)
    author_name: Mapped[str | None] = mapped_column(Text)
    known_rights_holder: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_reference_url: Mapped[str | None] = mapped_column(Text)
    default_language: Mapped[str] = mapped_column(String(16), default="zh-CN", server_default="zh-CN", nullable=False)
    target_language: Mapped[str] = mapped_column(String(16), default="vi-VN", server_default="vi-VN", nullable=False)
    rights_status: Mapped[str] = mapped_column(String(32), nullable=False)
    style_guide_text: Mapped[str | None] = mapped_column(Text)
    default_translator_profile_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("provider_profiles.id"))
    default_voice_preset_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("voice_presets.id"))
    monthly_chapter_target: Mapped[int] = mapped_column(Integer, default=50, server_default="50", nullable=False)
    archived_at: Mapped[object | None] = mapped_column(TZDateTime)


class RightsEvidence(MutableMixin, Base):
    __tablename__ = "rights_evidence"
    __table_args__ = (enum_constraint("evidence_kind", EvidenceKind), hash_constraint("sha256"))

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    project_id: Mapped[str] = mapped_column(UUID, ForeignKey("projects.id"), nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    issuer: Mapped[str | None] = mapped_column(Text)
    issued_at: Mapped[object | None] = mapped_column(TZDateTime)
    expires_at: Mapped[object | None] = mapped_column(TZDateTime)
    notes: Mapped[str | None] = mapped_column(Text)


class RightsGrant(MutableMixin, Base):
    __tablename__ = "rights_grants"
    __table_args__ = (
        UniqueConstraint("project_id", "scope", "territory", "valid_from", name="uq_rights_grants_project_scope_territory_valid_from"),
        enum_constraint("scope", RightsScope),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    project_id: Mapped[str] = mapped_column(UUID, ForeignKey("projects.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    territory: Mapped[str] = mapped_column(String(64), nullable=False)
    allows_ai_processing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allows_third_party_cloud: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    valid_from: Mapped[object] = mapped_column(TZDateTime, nullable=False)
    expires_at: Mapped[object | None] = mapped_column(TZDateTime)
    evidence_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("rights_evidence.id"))


class CloudProcessingConsent(MutableMixin, Base):
    __tablename__ = "cloud_processing_consents"
    __table_args__ = (enum_constraint("status", CloudConsentStatus),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    project_id: Mapped[str] = mapped_column(UUID, ForeignKey("projects.id"), nullable=False)
    provider_profile_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("provider_profiles.id"))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attestation_evidence_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("rights_evidence.id"))
    policy_snapshot_artifact_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("artifacts.id"))
    accepted_at: Mapped[object | None] = mapped_column(TZDateTime)
    revoked_at: Mapped[object | None] = mapped_column(TZDateTime)


class Chapter(MutableMixin, Base):
    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint("project_id", "ordinal", name="uq_chapters_project_ordinal"),
        CheckConstraint("ordinal > 0", name="ordinal_positive"),
        enum_constraint("state", ChapterState),
        Index("ix_chapters_project_state_ordinal", "project_id", "state", "ordinal"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    project_id: Mapped[str] = mapped_column(UUID, ForeignKey("projects.id"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_title: Mapped[str | None] = mapped_column(Text)
    translated_title: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_stage: Mapped[str | None] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    active_source_revision_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("source_revisions.id"))
    approved_translation_run_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("translation_runs.id"))
    active_voice_plan_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("voice_plans.id"))
    approved_master_artifact_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("artifacts.id"))
    last_export_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("exports.id"))
    translation_approved_at: Mapped[object | None] = mapped_column(TZDateTime)
    audio_approved_at: Mapped[object | None] = mapped_column(TZDateTime)


class SourceRevision(MutableMixin, Base):
    __tablename__ = "source_revisions"
    __table_args__ = (
        UniqueConstraint("chapter_id", "revision_no", name="uq_source_revisions_chapter_revision_no"),
        UniqueConstraint("chapter_id", "normalized_sha256", name="uq_source_revisions_chapter_normalized_sha256"),
        enum_constraint("import_kind", ImportKind),
        hash_constraint("normalized_sha256"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    chapter_id: Mapped[str] = mapped_column(UUID, ForeignKey("chapters.id"), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    import_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(Text)
    source_reference_url: Mapped[str | None] = mapped_column(Text)
    raw_artifact_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("artifacts.id"))
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    han_char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(64), nullable=False)


class SourceSegment(MutableMixin, Base):
    __tablename__ = "source_segments"
    __table_args__ = (
        UniqueConstraint("source_revision_id", "segment_index", name="uq_source_segments_revision_segment_index"),
        hash_constraint("source_sha256"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    source_revision_id: Mapped[str] = mapped_column(UUID, ForeignKey("source_revisions.id"), nullable=False)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    paragraph_start: Mapped[int] = mapped_column(Integer, nullable=False)
    paragraph_end: Mapped[int] = mapped_column(Integer, nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    segment_kind: Mapped[str] = mapped_column(String(32), nullable=False)


class GlossaryEntry(MutableMixin, Base):
    __tablename__ = "glossary_entries"

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    project_id: Mapped[str] = mapped_column(UUID, ForeignKey("projects.id"), nullable=False)
    source_term: Mapped[str] = mapped_column(Text, nullable=False)
    target_term: Mapped[str] = mapped_column(Text, nullable=False)
    reading: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64))
    gender: Mapped[str | None] = mapped_column(String(32))
    addressing_notes: Mapped[str | None] = mapped_column(Text)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("glossary_entries.id"))


class StoryMemoryEntry(MutableMixin, Base):
    __tablename__ = "story_memory_entries"

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    project_id: Mapped[str] = mapped_column(UUID, ForeignKey("projects.id"), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_to_ordinal: Mapped[int | None] = mapped_column(Integer)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)


class TranslationRun(MutableMixin, Base):
    __tablename__ = "translation_runs"
    __table_args__ = (
        enum_constraint("status", RunStatus),
        hash_constraint("glossary_revision_hash"),
        hash_constraint("story_memory_revision_hash"),
        hash_constraint("translation_text_sha256"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    chapter_id: Mapped[str] = mapped_column(UUID, ForeignKey("chapters.id"), nullable=False)
    source_revision_id: Mapped[str] = mapped_column(UUID, ForeignKey("source_revisions.id"), nullable=False)
    provider_profile_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("provider_profiles.id"))
    model: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    glossary_revision_hash: Mapped[str | None] = mapped_column(String(64))
    story_memory_revision_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    translation_text_sha256: Mapped[str | None] = mapped_column(String(64))
    estimated_cost_vnd: Mapped[int | None] = mapped_column(BigInteger)
    actual_cost_vnd: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[str] = mapped_column(String(64), default="LOCAL_OWNER", server_default="LOCAL_OWNER", nullable=False)


class TranslationSegment(MutableMixin, Base):
    __tablename__ = "translation_segments"
    __table_args__ = (
        UniqueConstraint("translation_run_id", "source_segment_id", name="uq_translation_segments_run_source_segment"),
        hash_constraint("target_sha256"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    translation_run_id: Mapped[str] = mapped_column(UUID, ForeignKey("translation_runs.id"), nullable=False)
    source_segment_id: Mapped[str] = mapped_column(UUID, ForeignKey("source_segments.id"), nullable=False)
    target_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(Text)
    cache_key: Mapped[str | None] = mapped_column(String(128))
    was_cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    manually_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    latency_ms: Mapped[int | None] = mapped_column(BigInteger)


class QaIssue(MutableMixin, Base):
    __tablename__ = "qa_issues"
    __table_args__ = (
        enum_constraint("category", QaCategory),
        enum_constraint("severity", QaSeverity),
        enum_constraint("status", QaStatus),
        CheckConstraint("status != 'ACCEPTED_RISK' OR severity IN ('MINOR', 'INFO')", name="accepted_risk_minor_info"),
        Index("ix_qa_issues_chapter_status_severity", "chapter_id", "status", "severity"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    chapter_id: Mapped[str] = mapped_column(UUID, ForeignKey("chapters.id"), nullable=False)
    translation_run_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("translation_runs.id"))
    speech_segment_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("speech_segments.id"))
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_segment_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("source_segments.id"))
    evidence: Mapped[str | None] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text)
    rule_or_model: Mapped[str | None] = mapped_column(Text)
    resolved_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[object | None] = mapped_column(TZDateTime)


class ProviderProfile(MutableMixin, Base):
    __tablename__ = "provider_profiles"
    __table_args__ = (enum_constraint("provider_kind", ProviderKind),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    provider_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    adapter_name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(255))
    region: Mapped[str | None] = mapped_column(String(64))
    secret_ref: Mapped[str | None] = mapped_column(String(255))
    config_json: Mapped[dict | None] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class VoicePreset(MutableMixin, Base):
    __tablename__ = "voice_presets"
    __table_args__ = (
        enum_constraint("origin", VoiceOrigin),
        CheckConstraint(
            "origin != 'USER_REFERENCE' OR consent_evidence_artifact_id IS NOT NULL",
            name="user_reference_requires_consent",
        ),
        hash_constraint("model_snapshot_hash"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    provider_profile_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("provider_profiles.id"))
    provider_voice_id: Mapped[str | None] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    gender_label: Mapped[str | None] = mapped_column(String(64))
    region_label: Mapped[str | None] = mapped_column(String(64))
    speed: Mapped[str] = mapped_column(String(32), nullable=False)
    pitch: Mapped[str] = mapped_column(String(32), nullable=False)
    style: Mapped[str | None] = mapped_column(String(64))
    sample_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    settings_json: Mapped[dict | None] = mapped_column(JSON)
    model_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    license_snapshot_artifact_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("artifacts.id"))
    consent_evidence_artifact_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("artifacts.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class VoicePlan(CreatedAtMixin, Base):
    __tablename__ = "voice_plans"
    __table_args__ = (
        UniqueConstraint("chapter_id", "revision_no", name="uq_voice_plans_chapter_revision_no"),
        enum_constraint("mode", VoiceMode),
        hash_constraint("plan_sha256"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    chapter_id: Mapped[str] = mapped_column(UUID, ForeignKey("chapters.id"), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    narrator_preset_id: Mapped[str] = mapped_column(UUID, ForeignKey("voice_presets.id"), nullable=False)
    plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class VoiceRole(MutableMixin, Base):
    __tablename__ = "voice_roles"

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    voice_plan_id: Mapped[str] = mapped_column(UUID, ForeignKey("voice_plans.id"), nullable=False)
    role_key: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    voice_preset_id: Mapped[str] = mapped_column(UUID, ForeignKey("voice_presets.id"), nullable=False)
    is_narrator: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SpeechSegment(MutableMixin, Base):
    __tablename__ = "speech_segments"
    __table_args__ = (
        UniqueConstraint("voice_plan_id", "segment_index", name="uq_speech_segments_plan_segment_index"),
        hash_constraint("narration_sha256"),
        hash_constraint("pronunciation_revision_hash"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    chapter_id: Mapped[str] = mapped_column(UUID, ForeignKey("chapters.id"), nullable=False)
    translation_run_id: Mapped[str] = mapped_column(UUID, ForeignKey("translation_runs.id"), nullable=False)
    voice_plan_id: Mapped[str] = mapped_column(UUID, ForeignKey("voice_plans.id"), nullable=False)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    translation_segment_id: Mapped[str] = mapped_column(UUID, ForeignKey("translation_segments.id"), nullable=False)
    role_id: Mapped[str] = mapped_column(UUID, ForeignKey("voice_roles.id"), nullable=False)
    narration_text: Mapped[str] = mapped_column(Text, nullable=False)
    narration_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pause_before_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pause_after_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pronunciation_revision_hash: Mapped[str | None] = mapped_column(String(64))
    estimated_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    synthesis_cache_key: Mapped[str | None] = mapped_column(String(128))


class Artifact(MutableMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        enum_constraint("kind", ArtifactKind),
        enum_constraint("status", ArtifactStatus),
        hash_constraint("sha256"),
        hash_constraint("input_hash"),
        hash_constraint("settings_hash"),
        Index("ix_artifacts_kind_input_settings_status", "kind", "input_hash", "settings_hash", "status"),
        Index(
            "uq_ready_artifact_cache",
            "kind",
            "input_hash",
            "settings_hash",
            unique=True,
            sqlite_where=text("status = 'READY'"),
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    chapter_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("chapters.id"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    producer: Mapped[str | None] = mapped_column(String(128))
    producer_version: Mapped[str | None] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    settings_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    supersedes_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("artifacts.id"))


class Job(MutableMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        enum_constraint("kind", JobKind),
        enum_constraint("status", JobStatus),
        Index("ix_jobs_status_next_priority", "status", "next_run_at", "priority"),
        Index("ix_jobs_lease_expires_at", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    project_id: Mapped[str] = mapped_column(UUID, ForeignKey("projects.id"), nullable=False)
    chapter_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("chapters.id"))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancel_requested_at: Mapped[object | None] = mapped_column(TZDateTime)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[object | None] = mapped_column(TZDateTime)
    next_run_at: Mapped[object | None] = mapped_column(TZDateTime)
    result_artifact_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("artifacts.id"))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)


class JobAttempt(MutableMixin, Base):
    __tablename__ = "job_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_no", name="uq_job_attempts_job_attempt_no"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    job_id: Mapped[str] = mapped_column(UUID, ForeignKey("jobs.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[object] = mapped_column(TZDateTime, nullable=False)
    heartbeat_at: Mapped[object | None] = mapped_column(TZDateTime)
    finished_at: Mapped[object | None] = mapped_column(TZDateTime)
    outcome: Mapped[str | None] = mapped_column(String(64))
    provider_request_id: Mapped[str | None] = mapped_column(Text)
    error_class: Mapped[str | None] = mapped_column(String(255))
    redacted_detail: Mapped[str | None] = mapped_column(Text)


class RateCard(MutableMixin, Base):
    __tablename__ = "rate_cards"
    __table_args__ = (enum_constraint("unit", UsageUnit),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str | None] = mapped_column(String(64))
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    price_usd_micros_per_million_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    effective_from: Mapped[object] = mapped_column(TZDateTime, nullable=False)
    effective_to: Mapped[object | None] = mapped_column(TZDateTime)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_note: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[object | None] = mapped_column(TZDateTime)


class BudgetAuthorization(MutableMixin, Base):
    __tablename__ = "budget_authorizations"
    __table_args__ = (CheckConstraint("status IN ('HELD', 'COMMITTED', 'RELEASED')", name="status_enum"),)

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    job_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("jobs.id"))
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    estimate_vnd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    contingency_vnd: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category: Mapped[str | None] = mapped_column(String(32))
    rate_card_ids_json: Mapped[list[str] | None] = mapped_column(JSON)
    expires_at: Mapped[object] = mapped_column(TZDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class UsageLedger(CreatedAtMixin, Base):
    __tablename__ = "usage_ledger"
    __table_args__ = (
        enum_constraint("unit", UsageUnit),
        CheckConstraint("billing_confidence IN ('CONFIRMED', 'ESTIMATED', 'UNKNOWN')", name="billing_confidence_enum"),
        Index("ix_usage_ledger_created_provider_model", "created_at", "provider", "model"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str | None] = mapped_column(String(64))
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    measured_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rate_card_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("rate_cards.id"))
    actual_usd_micros: Mapped[int | None] = mapped_column(BigInteger)
    fx_rate: Mapped[int | None] = mapped_column(BigInteger)
    actual_vnd: Mapped[int | None] = mapped_column(BigInteger)
    billing_confidence: Mapped[str] = mapped_column(String(16), nullable=False)


class Export(CreatedAtMixin, Base):
    __tablename__ = "exports"
    __table_args__ = (
        enum_constraint("kind", ExportKind),
        enum_constraint("status", ExportStatus),
        hash_constraint("manifest_sha256"),
        Index(
            "uq_ready_export_manifest",
            "chapter_id",
            "kind",
            "manifest_sha256",
            unique=True,
            sqlite_where=text("status = 'READY' AND manifest_sha256 IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    chapter_id: Mapped[str] = mapped_column(UUID, ForeignKey("chapters.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    bundle_artifact_id: Mapped[str | None] = mapped_column(UUID, ForeignKey("artifacts.id"))
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    rights_evaluation_json: Mapped[dict | None] = mapped_column(JSON)
    revoked_at: Mapped[object | None] = mapped_column(TZDateTime)


class AuditEvent(CreatedAtMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("actor IN ('LOCAL_OWNER', 'WORKER')", name="actor_enum"),
        hash_constraint("before_hash"),
        hash_constraint("after_hash"),
    )

    id: Mapped[str] = mapped_column(UUID, primary_key=True)
    actor: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    before_hash: Mapped[str | None] = mapped_column(String(64))
    after_hash: Mapped[str | None] = mapped_column(String(64))
    redacted_details: Mapped[str | None] = mapped_column(Text)


class EventLog(CreatedAtMixin, Base):
    __tablename__ = "event_log"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", name="uq_event_log_entity"),)

    sequence_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[str] = mapped_column(UUID, nullable=False)
