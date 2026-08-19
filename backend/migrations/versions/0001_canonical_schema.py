from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


UUID = sa.String(36)
DT = sa.String(32)


def enum_check(column_name: str, values: tuple[str, ...]) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(f"{column_name} IN ({allowed})", name=f"{column_name}_enum")


def hash_check(column_name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column_name} IS NULL OR (length({column_name}) = 64 AND {column_name} NOT GLOB '*[^0-9a-f]*')",
        name=f"{column_name}_lowercase_sha256",
    )


def id_column() -> sa.Column:
    return sa.Column("id", UUID, primary_key=True)


def created_at() -> sa.Column:
    return sa.Column("created_at", DT, nullable=False)


def updated_at() -> sa.Column:
    return sa.Column("updated_at", DT, nullable=False)


def upgrade() -> None:
    op.create_table(
        "projects",
        id_column(),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("original_title", sa.Text()),
        sa.Column("author_name", sa.Text()),
        sa.Column("known_rights_holder", sa.Text()),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_reference_url", sa.Text()),
        sa.Column("default_language", sa.String(16), server_default="zh-CN", nullable=False),
        sa.Column("target_language", sa.String(16), server_default="vi-VN", nullable=False),
        sa.Column("rights_status", sa.String(32), nullable=False),
        sa.Column("style_guide_text", sa.Text()),
        sa.Column("default_translator_profile_id", UUID, sa.ForeignKey("provider_profiles.id")),
        sa.Column("default_voice_preset_id", UUID, sa.ForeignKey("voice_presets.id")),
        sa.Column("monthly_chapter_target", sa.Integer(), server_default="50", nullable=False),
        sa.Column("archived_at", DT),
        created_at(),
        updated_at(),
        sa.UniqueConstraint("slug", name="uq_projects_slug"),
        enum_check(
            "source_type",
            (
                "SELF_AUTHORED",
                "PUBLIC_DOMAIN",
                "OPEN_LICENSE",
                "LICENSED_PARTNER",
                "USER_SUPPLIED_PRIVATE",
                "UNKNOWN",
            ),
        ),
        enum_check("rights_status", ("PRIVATE_ONLY", "REVIEW_REQUIRED", "CLEARED", "EXPIRED", "BLOCKED")),
    )
    op.create_table(
        "rights_evidence",
        id_column(),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("evidence_kind", sa.String(32), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("issuer", sa.Text()),
        sa.Column("issued_at", DT),
        sa.Column("expires_at", DT),
        sa.Column("notes", sa.Text()),
        created_at(),
        updated_at(),
        enum_check(
            "evidence_kind",
            (
                "CONTRACT",
                "LICENSE",
                "AUTHOR_PERMISSION",
                "PUBLIC_DOMAIN_PROOF",
                "OPEN_LICENSE_SNAPSHOT",
                "USER_ATTESTATION",
                "VOICE_CONSENT",
                "MODEL_LICENSE_SNAPSHOT",
            ),
        ),
        hash_check("sha256"),
    )
    op.create_table(
        "rights_grants",
        id_column(),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("territory", sa.String(64), nullable=False),
        sa.Column("allows_ai_processing", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("allows_third_party_cloud", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("valid_from", DT, nullable=False),
        sa.Column("expires_at", DT),
        sa.Column("evidence_id", UUID, sa.ForeignKey("rights_evidence.id")),
        created_at(),
        updated_at(),
        sa.UniqueConstraint(
            "project_id",
            "scope",
            "territory",
            "valid_from",
            name="uq_rights_grants_project_scope_territory_valid_from",
        ),
        enum_check("scope", ("TRANSLATE_VI", "CREATE_AUDIO", "PUBLIC_STREAM", "DOWNLOAD", "MONETIZE")),
    )
    op.create_table(
        "cloud_processing_consents",
        id_column(),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("provider_profile_id", UUID, sa.ForeignKey("provider_profiles.id")),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attestation_evidence_id", UUID, sa.ForeignKey("rights_evidence.id")),
        sa.Column("policy_snapshot_artifact_id", UUID, sa.ForeignKey("artifacts.id")),
        sa.Column("accepted_at", DT),
        sa.Column("revoked_at", DT),
        created_at(),
        updated_at(),
        enum_check("status", ("NOT_GRANTED", "GRANTED", "REVOKED")),
    )
    op.create_table(
        "chapters",
        id_column(),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_title", sa.Text()),
        sa.Column("translated_title", sa.Text()),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("failure_stage", sa.String(64)),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("active_source_revision_id", UUID, sa.ForeignKey("source_revisions.id")),
        sa.Column("approved_translation_run_id", UUID, sa.ForeignKey("translation_runs.id")),
        sa.Column("active_voice_plan_id", UUID, sa.ForeignKey("voice_plans.id")),
        sa.Column("approved_master_artifact_id", UUID, sa.ForeignKey("artifacts.id")),
        sa.Column("last_export_id", UUID, sa.ForeignKey("exports.id")),
        sa.Column("translation_approved_at", DT),
        sa.Column("audio_approved_at", DT),
        created_at(),
        updated_at(),
        sa.UniqueConstraint("project_id", "ordinal", name="uq_chapters_project_ordinal"),
        sa.CheckConstraint("ordinal > 0", name="ordinal_positive"),
        enum_check(
            "state",
            (
                "IMPORTED",
                "NORMALIZED",
                "TRANSLATING",
                "TRANSLATION_REVIEW",
                "TRANSLATION_APPROVED",
                "VOICE_CONFIGURED",
                "TTS_QUEUED",
                "SYNTHESIZING",
                "AUDIO_REVIEW",
                "READY_TO_EXPORT",
                "EXPORTED",
                "FAILED",
            ),
        ),
    )
    op.create_index("ix_chapters_project_state_ordinal", "chapters", ["project_id", "state", "ordinal"])
    op.create_table(
        "source_revisions",
        id_column(),
        sa.Column("chapter_id", UUID, sa.ForeignKey("chapters.id"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("import_kind", sa.String(32), nullable=False),
        sa.Column("original_filename", sa.Text()),
        sa.Column("source_reference_url", sa.Text()),
        sa.Column("raw_artifact_id", UUID, sa.ForeignKey("artifacts.id")),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("normalized_sha256", sa.String(64), nullable=False),
        sa.Column("han_char_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_char_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("normalizer_version", sa.String(64), nullable=False),
        created_at(),
        updated_at(),
        sa.UniqueConstraint("chapter_id", "revision_no", name="uq_source_revisions_chapter_revision_no"),
        sa.UniqueConstraint("chapter_id", "normalized_sha256", name="uq_source_revisions_chapter_normalized_sha256"),
        enum_check("import_kind", ("PASTE", "TXT", "EPUB", "DOCX", "LOCAL_FOLDER", "OFFICIAL_YUEWEN_API")),
        hash_check("normalized_sha256"),
    )
    op.create_table(
        "source_segments",
        id_column(),
        sa.Column("source_revision_id", UUID, sa.ForeignKey("source_revisions.id"), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("paragraph_start", sa.Integer(), nullable=False),
        sa.Column("paragraph_end", sa.Integer(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("segment_kind", sa.String(32), nullable=False),
        created_at(),
        updated_at(),
        sa.UniqueConstraint("source_revision_id", "segment_index", name="uq_source_segments_revision_segment_index"),
        hash_check("source_sha256"),
    )
    op.create_table(
        "glossary_entries",
        id_column(),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("source_term", sa.Text(), nullable=False),
        sa.Column("target_term", sa.Text(), nullable=False),
        sa.Column("reading", sa.Text()),
        sa.Column("category", sa.String(64)),
        sa.Column("gender", sa.String(32)),
        sa.Column("addressing_notes", sa.Text()),
        sa.Column("is_locked", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", UUID, sa.ForeignKey("glossary_entries.id")),
        created_at(),
        updated_at(),
    )
    op.create_table(
        "story_memory_entries",
        id_column(),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("entity_key", sa.String(255), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("valid_from_ordinal", sa.Integer(), nullable=False),
        sa.Column("valid_to_ordinal", sa.Integer()),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        created_at(),
        updated_at(),
    )
    op.create_table(
        "translation_runs",
        id_column(),
        sa.Column("chapter_id", UUID, sa.ForeignKey("chapters.id"), nullable=False),
        sa.Column("source_revision_id", UUID, sa.ForeignKey("source_revisions.id"), nullable=False),
        sa.Column("provider_profile_id", UUID, sa.ForeignKey("provider_profiles.id")),
        sa.Column("model", sa.String(255)),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("glossary_revision_hash", sa.String(64)),
        sa.Column("story_memory_revision_hash", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("translation_text_sha256", sa.String(64)),
        sa.Column("estimated_cost_vnd", sa.BigInteger()),
        sa.Column("actual_cost_vnd", sa.BigInteger()),
        sa.Column("created_by", sa.String(64), server_default="LOCAL_OWNER", nullable=False),
        created_at(),
        updated_at(),
        enum_check("status", ("PENDING", "RUNNING", "REVIEW", "APPROVED", "SUPERSEDED", "FAILED")),
        hash_check("glossary_revision_hash"),
        hash_check("story_memory_revision_hash"),
        hash_check("translation_text_sha256"),
    )
    op.create_table(
        "translation_segments",
        id_column(),
        sa.Column("translation_run_id", UUID, sa.ForeignKey("translation_runs.id"), nullable=False),
        sa.Column("source_segment_id", UUID, sa.ForeignKey("source_segments.id"), nullable=False),
        sa.Column("target_text", sa.Text(), nullable=False),
        sa.Column("target_sha256", sa.String(64), nullable=False),
        sa.Column("provider_request_id", sa.Text()),
        sa.Column("cache_key", sa.String(128)),
        sa.Column("was_cache_hit", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("manually_edited", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("input_tokens", sa.BigInteger()),
        sa.Column("output_tokens", sa.BigInteger()),
        sa.Column("latency_ms", sa.BigInteger()),
        created_at(),
        updated_at(),
        sa.UniqueConstraint("translation_run_id", "source_segment_id", name="uq_translation_segments_run_source_segment"),
        hash_check("target_sha256"),
    )
    op.create_table(
        "qa_issues",
        id_column(),
        sa.Column("chapter_id", UUID, sa.ForeignKey("chapters.id"), nullable=False),
        sa.Column("translation_run_id", UUID, sa.ForeignKey("translation_runs.id")),
        sa.Column("speech_segment_id", UUID, sa.ForeignKey("speech_segments.id")),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source_segment_id", UUID, sa.ForeignKey("source_segments.id")),
        sa.Column("evidence", sa.Text()),
        sa.Column("suggestion", sa.Text()),
        sa.Column("rule_or_model", sa.Text()),
        sa.Column("resolved_note", sa.Text()),
        sa.Column("resolved_at", DT),
        created_at(),
        updated_at(),
        enum_check(
            "category",
            (
                "COMPLETENESS",
                "ACCURACY",
                "TERMINOLOGY",
                "NAME",
                "PRONOUN_GENDER",
                "NUMBER_UNIT",
                "RESIDUAL_HAN",
                "REPETITION",
                "META_TEXT",
                "STYLE",
                "TTS_LENGTH",
                "AUDIO_TECHNICAL",
                "PRONUNCIATION",
                "SILENCE",
                "CLIPPING",
            ),
        ),
        enum_check("severity", ("INFO", "MINOR", "MAJOR", "CRITICAL")),
        enum_check("status", ("OPEN", "ACCEPTED_RISK", "FIXED", "DISMISSED")),
        sa.CheckConstraint(
            "status != 'ACCEPTED_RISK' OR severity IN ('MINOR', 'INFO')",
            name="accepted_risk_minor_info",
        ),
    )
    op.create_index("ix_qa_issues_chapter_status_severity", "qa_issues", ["chapter_id", "status", "severity"])
    op.create_table(
        "provider_profiles",
        id_column(),
        sa.Column("provider_kind", sa.String(32), nullable=False),
        sa.Column("adapter_name", sa.String(128), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("model", sa.String(255)),
        sa.Column("region", sa.String(64)),
        sa.Column("secret_ref", sa.String(255)),
        sa.Column("config_json", sa.JSON()),
        sa.Column("enabled", sa.Boolean(), server_default="0", nullable=False),
        created_at(),
        updated_at(),
        enum_check("provider_kind", ("TRANSLATOR", "REVIEWER", "TTS", "ASR")),
    )
    op.create_table(
        "voice_presets",
        id_column(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider_profile_id", UUID, sa.ForeignKey("provider_profiles.id")),
        sa.Column("provider_voice_id", sa.Text()),
        sa.Column("locale", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("gender_label", sa.String(64)),
        sa.Column("region_label", sa.String(64)),
        sa.Column("speed", sa.String(32), nullable=False),
        sa.Column("pitch", sa.String(32), nullable=False),
        sa.Column("style", sa.String(64)),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("settings_json", sa.JSON()),
        sa.Column("model_snapshot_hash", sa.String(64)),
        sa.Column("license_snapshot_artifact_id", UUID, sa.ForeignKey("artifacts.id")),
        sa.Column("consent_evidence_artifact_id", UUID, sa.ForeignKey("artifacts.id")),
        sa.Column("active", sa.Boolean(), server_default="0", nullable=False),
        created_at(),
        updated_at(),
        enum_check("origin", ("BUILT_IN", "CLOUD_CATALOG", "USER_REFERENCE")),
        sa.CheckConstraint(
            "origin != 'USER_REFERENCE' OR consent_evidence_artifact_id IS NOT NULL",
            name="user_reference_requires_consent",
        ),
        hash_check("model_snapshot_hash"),
    )
    op.create_table(
        "voice_plans",
        id_column(),
        sa.Column("chapter_id", UUID, sa.ForeignKey("chapters.id"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("narrator_preset_id", UUID, sa.ForeignKey("voice_presets.id"), nullable=False),
        sa.Column("plan_sha256", sa.String(64), nullable=False),
        created_at(),
        sa.UniqueConstraint("chapter_id", "revision_no", name="uq_voice_plans_chapter_revision_no"),
        enum_check("mode", ("SINGLE_NARRATOR", "ASSISTED_MULTI_VOICE")),
        hash_check("plan_sha256"),
    )
    op.create_table(
        "voice_roles",
        id_column(),
        sa.Column("voice_plan_id", UUID, sa.ForeignKey("voice_plans.id"), nullable=False),
        sa.Column("role_key", sa.String(128), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("voice_preset_id", UUID, sa.ForeignKey("voice_presets.id"), nullable=False),
        sa.Column("is_narrator", sa.Boolean(), server_default="0", nullable=False),
        created_at(),
        updated_at(),
    )
    op.create_table(
        "speech_segments",
        id_column(),
        sa.Column("chapter_id", UUID, sa.ForeignKey("chapters.id"), nullable=False),
        sa.Column("translation_run_id", UUID, sa.ForeignKey("translation_runs.id"), nullable=False),
        sa.Column("voice_plan_id", UUID, sa.ForeignKey("voice_plans.id"), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False),
        sa.Column("translation_segment_id", UUID, sa.ForeignKey("translation_segments.id"), nullable=False),
        sa.Column("role_id", UUID, sa.ForeignKey("voice_roles.id"), nullable=False),
        sa.Column("narration_text", sa.Text(), nullable=False),
        sa.Column("narration_sha256", sa.String(64), nullable=False),
        sa.Column("pause_before_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("pause_after_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("pronunciation_revision_hash", sa.String(64)),
        sa.Column("estimated_duration_ms", sa.BigInteger()),
        sa.Column("synthesis_cache_key", sa.String(128)),
        created_at(),
        updated_at(),
        sa.UniqueConstraint("voice_plan_id", "segment_index", name="uq_speech_segments_plan_segment_index"),
        hash_check("narration_sha256"),
        hash_check("pronunciation_revision_hash"),
    )
    op.create_table(
        "artifacts",
        id_column(),
        sa.Column("chapter_id", UUID, sa.ForeignKey("chapters.id")),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(255), nullable=False),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("producer", sa.String(128)),
        sa.Column("producer_version", sa.String(64)),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("settings_hash", sa.String(64), nullable=False),
        sa.Column("metadata_json", sa.JSON()),
        sa.Column("supersedes_id", UUID, sa.ForeignKey("artifacts.id")),
        created_at(),
        updated_at(),
        enum_check(
            "kind",
            (
                "SOURCE_SNAPSHOT",
                "TRANSLATION_MARKDOWN",
                "TTS_SEGMENT",
                "VOICE_PREVIEW",
                "MASTER_WAV",
                "MASTER_MP3",
                "SRT",
                "REPORT",
                "RIGHTS_EVIDENCE",
                "LICENSE_SNAPSHOT",
                "CONSENT_EVIDENCE",
                "PRIVATE_ARCHIVE",
                "PUBLICATION_BUNDLE",
            ),
        ),
        enum_check("status", ("WRITING", "READY", "CORRUPT", "SUPERSEDED", "DELETED")),
        hash_check("sha256"),
        hash_check("input_hash"),
        hash_check("settings_hash"),
    )
    op.create_index(
        "ix_artifacts_kind_input_settings_status",
        "artifacts",
        ["kind", "input_hash", "settings_hash", "status"],
    )
    op.create_index(
        "uq_ready_artifact_cache",
        "artifacts",
        ["kind", "input_hash", "settings_hash"],
        unique=True,
        sqlite_where=sa.text("status = 'READY'"),
    )
    op.create_table(
        "jobs",
        id_column(),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("project_id", UUID, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("chapter_id", UUID, sa.ForeignKey("chapters.id")),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("progress_current", sa.Integer(), server_default="0", nullable=False),
        sa.Column("progress_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cancel_requested_at", DT),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", DT),
        sa.Column("next_run_at", DT),
        sa.Column("result_artifact_id", UUID, sa.ForeignKey("artifacts.id")),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_summary", sa.Text()),
        created_at(),
        updated_at(),
        sa.UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        enum_check(
            "kind",
            (
                "IMPORT",
                "TRANSLATE",
                "REVIEW",
                "REPAIR_TRANSLATION",
                "PREVIEW_TTS",
                "SYNTHESIZE",
                "MASTER",
                "AUDIO_QA",
                "EXPORT",
            ),
        ),
        enum_check(
            "status",
            (
                "QUEUED",
                "RUNNING",
                "CANCEL_REQUESTED",
                "SUCCEEDED",
                "FAILED",
                "CANCELED",
                "BLOCKED_BUDGET",
                "BILLING_UNKNOWN",
            ),
        ),
    )
    op.create_index("ix_jobs_status_next_priority", "jobs", ["status", "next_run_at", "priority"])
    op.create_index("ix_jobs_lease_expires_at", "jobs", ["lease_expires_at"])
    op.create_table(
        "job_attempts",
        id_column(),
        sa.Column("job_id", UUID, sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("started_at", DT, nullable=False),
        sa.Column("heartbeat_at", DT),
        sa.Column("finished_at", DT),
        sa.Column("outcome", sa.String(64)),
        sa.Column("provider_request_id", sa.Text()),
        sa.Column("error_class", sa.String(255)),
        sa.Column("redacted_detail", sa.Text()),
        created_at(),
        updated_at(),
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_job_attempts_job_attempt_no"),
    )
    op.create_table(
        "rate_cards",
        id_column(),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("region", sa.String(64)),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("price_usd_micros_per_million_units", sa.BigInteger(), nullable=False),
        sa.Column("effective_from", DT, nullable=False),
        sa.Column("effective_to", DT),
        sa.Column("source_url", sa.Text()),
        sa.Column("source_note", sa.Text()),
        sa.Column("verified_at", DT),
        created_at(),
        updated_at(),
        enum_check(
            "unit",
            ("INPUT_TOKEN", "OUTPUT_TOKEN", "CHARACTER", "UTF8_BYTE", "AUDIO_TOKEN", "AUDIO_SECOND", "REQUEST"),
        ),
    )
    op.create_table(
        "budget_authorizations",
        id_column(),
        sa.Column("job_id", UUID, sa.ForeignKey("jobs.id")),
        sa.Column("operation_id", sa.String(128), nullable=False),
        sa.Column("estimate_vnd", sa.BigInteger(), nullable=False),
        sa.Column("contingency_vnd", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", DT, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        created_at(),
        updated_at(),
        sa.CheckConstraint("status IN ('HELD', 'COMMITTED', 'RELEASED')", name="status_enum"),
    )
    op.create_table(
        "usage_ledger",
        id_column(),
        sa.Column("provider", sa.String(128), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("region", sa.String(64)),
        sa.Column("operation_id", sa.String(128), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("measured_units", sa.BigInteger(), nullable=False),
        sa.Column("rate_card_id", UUID, sa.ForeignKey("rate_cards.id")),
        sa.Column("actual_usd_micros", sa.BigInteger()),
        sa.Column("fx_rate", sa.BigInteger()),
        sa.Column("actual_vnd", sa.BigInteger()),
        sa.Column("billing_confidence", sa.String(16), nullable=False),
        created_at(),
        enum_check(
            "unit",
            ("INPUT_TOKEN", "OUTPUT_TOKEN", "CHARACTER", "UTF8_BYTE", "AUDIO_TOKEN", "AUDIO_SECOND", "REQUEST"),
        ),
        sa.CheckConstraint("billing_confidence IN ('CONFIRMED', 'ESTIMATED', 'UNKNOWN')", name="billing_confidence_enum"),
    )
    op.create_index("ix_usage_ledger_created_provider_model", "usage_ledger", ["created_at", "provider", "model"])
    op.create_table(
        "exports",
        id_column(),
        sa.Column("chapter_id", UUID, sa.ForeignKey("chapters.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("bundle_artifact_id", UUID, sa.ForeignKey("artifacts.id")),
        sa.Column("manifest_sha256", sa.String(64)),
        sa.Column("rights_evaluation_json", sa.JSON()),
        created_at(),
        sa.Column("revoked_at", DT),
        enum_check("kind", ("PRIVATE_ARCHIVE", "PUBLICATION_BUNDLE")),
        enum_check("status", ("BUILDING", "READY", "FAILED", "REVOKED")),
        hash_check("manifest_sha256"),
    )
    op.create_table(
        "audit_events",
        id_column(),
        sa.Column("actor", sa.String(32), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("entity_type", sa.String(128), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("before_hash", sa.String(64)),
        sa.Column("after_hash", sa.String(64)),
        sa.Column("redacted_details", sa.Text()),
        created_at(),
        sa.CheckConstraint("actor IN ('LOCAL_OWNER', 'WORKER')", name="actor_enum"),
        hash_check("before_hash"),
        hash_check("after_hash"),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("exports")
    op.drop_index("ix_usage_ledger_created_provider_model", table_name="usage_ledger")
    op.drop_table("usage_ledger")
    op.drop_table("budget_authorizations")
    op.drop_table("rate_cards")
    op.drop_table("job_attempts")
    op.drop_index("ix_jobs_lease_expires_at", table_name="jobs")
    op.drop_index("ix_jobs_status_next_priority", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("uq_ready_artifact_cache", table_name="artifacts")
    op.drop_index("ix_artifacts_kind_input_settings_status", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_table("speech_segments")
    op.drop_table("voice_roles")
    op.drop_table("voice_plans")
    op.drop_table("voice_presets")
    op.drop_table("provider_profiles")
    op.drop_index("ix_qa_issues_chapter_status_severity", table_name="qa_issues")
    op.drop_table("qa_issues")
    op.drop_table("translation_segments")
    op.drop_table("translation_runs")
    op.drop_table("story_memory_entries")
    op.drop_table("glossary_entries")
    op.drop_table("source_segments")
    op.drop_table("source_revisions")
    op.drop_index("ix_chapters_project_state_ordinal", table_name="chapters")
    op.drop_table("chapters")
    op.drop_table("cloud_processing_consents")
    op.drop_table("rights_grants")
    op.drop_table("rights_evidence")
    op.drop_table("projects")
