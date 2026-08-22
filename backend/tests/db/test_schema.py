from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import StatementError

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
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


EXPECTED_TABLES = {
    "projects",
    "rights_evidence",
    "rights_grants",
    "cloud_processing_consents",
    "chapters",
    "source_revisions",
    "source_segments",
    "glossary_entries",
    "story_memory_entries",
    "translation_runs",
    "translation_segments",
    "qa_issues",
    "provider_profiles",
    "voice_presets",
    "voice_plans",
    "voice_roles",
    "speech_segments",
    "artifacts",
    "jobs",
    "job_attempts",
    "rate_cards",
    "budget_authorizations",
    "usage_ledger",
    "exports",
    "audit_events",
    "event_log",
}


EXPECTED_ENUMS = {
    SourceType: {
        "SELF_AUTHORED",
        "PUBLIC_DOMAIN",
        "OPEN_LICENSE",
        "LICENSED_PARTNER",
        "USER_SUPPLIED_PRIVATE",
        "UNKNOWN",
    },
    ImportKind: {"PASTE", "TXT", "EPUB", "DOCX", "LOCAL_FOLDER", "OFFICIAL_YUEWEN_API"},
    RightsStatus: {"PRIVATE_ONLY", "REVIEW_REQUIRED", "CLEARED", "EXPIRED", "BLOCKED"},
    RightsScope: {"TRANSLATE_VI", "CREATE_AUDIO", "PUBLIC_STREAM", "DOWNLOAD", "MONETIZE"},
    EvidenceKind: {
        "CONTRACT",
        "LICENSE",
        "AUTHOR_PERMISSION",
        "PUBLIC_DOMAIN_PROOF",
        "OPEN_LICENSE_SNAPSHOT",
        "USER_ATTESTATION",
        "VOICE_CONSENT",
        "MODEL_LICENSE_SNAPSHOT",
    },
    CloudConsentStatus: {"NOT_GRANTED", "GRANTED", "REVOKED"},
    JobKind: {
        "IMPORT",
        "TRANSLATE",
        "REVIEW",
        "REPAIR_TRANSLATION",
        "PREVIEW_TTS",
        "SYNTHESIZE",
        "MASTER",
        "AUDIO_QA",
        "EXPORT",
    },
    JobStatus: {
        "QUEUED",
        "RUNNING",
        "CANCEL_REQUESTED",
        "SUCCEEDED",
        "FAILED",
        "CANCELED",
        "BLOCKED_BUDGET",
        "BILLING_UNKNOWN",
    },
    RunStatus: {"PENDING", "RUNNING", "REVIEW", "APPROVED", "SUPERSEDED", "FAILED"},
    ProviderKind: {"TRANSLATOR", "REVIEWER", "TTS", "ASR"},
    QaCategory: {
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
    },
    QaSeverity: {"INFO", "MINOR", "MAJOR", "CRITICAL"},
    QaStatus: {"OPEN", "ACCEPTED_RISK", "FIXED", "DISMISSED"},
    VoiceMode: {"SINGLE_NARRATOR", "ASSISTED_MULTI_VOICE"},
    VoiceOrigin: {"BUILT_IN", "CLOUD_CATALOG", "USER_REFERENCE"},
    ArtifactKind: {
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
    },
    ArtifactStatus: {"WRITING", "READY", "CORRUPT", "SUPERSEDED", "DELETED"},
    ExportKind: {"PRIVATE_ARCHIVE", "PUBLICATION_BUNDLE"},
    ExportStatus: {"BUILDING", "READY", "FAILED", "REVOKED"},
    UsageUnit: {
        "INPUT_TOKEN",
        "OUTPUT_TOKEN",
        "CHARACTER",
        "UTF8_BYTE",
        "AUDIO_TOKEN",
        "AUDIO_SECOND",
        "REQUEST",
    },
}


EXPECTED_COLUMNS = {
    "projects": {
        "id",
        "title",
        "slug",
        "original_title",
        "author_name",
        "known_rights_holder",
        "source_type",
        "source_reference_url",
        "default_language",
        "target_language",
        "rights_status",
        "style_guide_text",
        "default_translator_profile_id",
        "default_voice_preset_id",
        "monthly_chapter_target",
        "archived_at",
        "created_at",
        "updated_at",
    },
    "rights_evidence": {
        "id",
        "project_id",
        "evidence_kind",
        "display_name",
        "relative_path",
        "sha256",
        "issuer",
        "issued_at",
        "expires_at",
        "notes",
        "created_at",
        "updated_at",
    },
    "rights_grants": {
        "id",
        "project_id",
        "scope",
        "territory",
        "allows_ai_processing",
        "allows_third_party_cloud",
        "valid_from",
        "expires_at",
        "evidence_id",
        "created_at",
        "updated_at",
    },
    "cloud_processing_consents": {
        "id",
        "project_id",
        "provider_profile_id",
        "status",
        "attestation_evidence_id",
        "policy_snapshot_artifact_id",
        "accepted_at",
        "revoked_at",
        "created_at",
        "updated_at",
    },
    "chapters": {
        "id",
        "project_id",
        "ordinal",
        "source_title",
        "translated_title",
        "state",
        "failure_stage",
        "failure_code",
        "active_source_revision_id",
        "approved_translation_run_id",
        "active_voice_plan_id",
        "approved_master_artifact_id",
        "last_export_id",
        "translation_approved_at",
        "audio_approved_at",
        "created_at",
        "updated_at",
    },
    "source_revisions": {
        "id",
        "chapter_id",
        "revision_no",
        "import_kind",
        "original_filename",
        "source_reference_url",
        "raw_artifact_id",
        "normalized_text",
        "normalized_sha256",
        "han_char_count",
        "total_char_count",
        "normalizer_version",
        "created_at",
        "updated_at",
    },
    "source_segments": {
        "id",
        "source_revision_id",
        "segment_index",
        "paragraph_start",
        "paragraph_end",
        "source_text",
        "source_sha256",
        "segment_kind",
        "created_at",
        "updated_at",
    },
    "glossary_entries": {
        "id",
        "project_id",
        "source_term",
        "target_term",
        "reading",
        "category",
        "gender",
        "addressing_notes",
        "is_locked",
        "revision_no",
        "supersedes_id",
        "created_at",
        "updated_at",
    },
    "story_memory_entries": {
        "id",
        "project_id",
        "entity_key",
        "entity_type",
        "summary",
        "valid_from_ordinal",
        "valid_to_ordinal",
        "revision_no",
        "created_at",
        "updated_at",
    },
    "translation_runs": {
        "id",
        "chapter_id",
        "source_revision_id",
        "provider_profile_id",
        "model",
        "prompt_version",
        "glossary_revision_hash",
        "story_memory_revision_hash",
        "status",
        "translation_text_sha256",
        "estimated_cost_vnd",
        "actual_cost_vnd",
        "created_by",
        "created_at",
        "updated_at",
    },
    "translation_segments": {
        "id",
        "translation_run_id",
        "source_segment_id",
        "target_text",
        "target_sha256",
        "provider_request_id",
        "cache_key",
        "was_cache_hit",
        "manually_edited",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "created_at",
        "updated_at",
    },
    "qa_issues": {
        "id",
        "chapter_id",
        "translation_run_id",
        "speech_segment_id",
        "category",
        "severity",
        "status",
        "source_segment_id",
        "evidence",
        "suggestion",
        "rule_or_model",
        "resolved_note",
        "resolved_at",
        "created_at",
        "updated_at",
    },
    "provider_profiles": {
        "id",
        "provider_kind",
        "adapter_name",
        "display_name",
        "model",
        "region",
        "secret_ref",
        "config_json",
        "enabled",
        "created_at",
        "updated_at",
    },
    "voice_presets": {
        "id",
        "name",
        "provider_profile_id",
        "provider_voice_id",
        "locale",
        "origin",
        "gender_label",
        "region_label",
        "speed",
        "pitch",
        "style",
        "sample_rate",
        "settings_json",
        "model_snapshot_hash",
        "license_snapshot_artifact_id",
        "consent_evidence_artifact_id",
        "active",
        "created_at",
        "updated_at",
    },
    "voice_plans": {
        "id",
        "chapter_id",
        "revision_no",
        "mode",
        "narrator_preset_id",
        "plan_sha256",
        "created_at",
    },
    "voice_roles": {
        "id",
        "voice_plan_id",
        "role_key",
        "display_name",
        "voice_preset_id",
        "is_narrator",
        "created_at",
        "updated_at",
    },
    "speech_segments": {
        "id",
        "chapter_id",
        "translation_run_id",
        "voice_plan_id",
        "segment_index",
        "translation_segment_id",
        "role_id",
        "narration_text",
        "narration_sha256",
        "pause_before_ms",
        "pause_after_ms",
        "pronunciation_revision_hash",
        "estimated_duration_ms",
        "synthesis_cache_key",
        "created_at",
        "updated_at",
    },
    "artifacts": {
        "id",
        "chapter_id",
        "kind",
        "status",
        "relative_path",
        "sha256",
        "byte_size",
        "mime_type",
        "duration_ms",
        "producer",
        "producer_version",
        "input_hash",
        "settings_hash",
        "metadata_json",
        "supersedes_id",
        "created_at",
        "updated_at",
    },
    "jobs": {
        "id",
        "kind",
        "status",
        "project_id",
        "chapter_id",
        "idempotency_key",
        "priority",
        "progress_current",
        "progress_total",
        "cancel_requested_at",
        "lease_owner",
        "lease_expires_at",
        "next_run_at",
        "result_artifact_id",
        "error_code",
        "error_summary",
        "created_at",
        "updated_at",
    },
    "job_attempts": {
        "id",
        "job_id",
        "attempt_no",
        "started_at",
        "heartbeat_at",
        "finished_at",
        "outcome",
        "provider_request_id",
        "error_class",
        "redacted_detail",
        "created_at",
        "updated_at",
    },
    "rate_cards": {
        "id",
        "provider",
        "model",
        "region",
        "unit",
        "price_usd_micros_per_million_units",
        "effective_from",
        "effective_to",
        "source_url",
        "source_note",
        "verified_at",
        "created_at",
        "updated_at",
    },
    "budget_authorizations": {
        "id",
        "job_id",
        "operation_id",
        "estimate_vnd",
        "contingency_vnd",
        "expires_at",
        "status",
        "created_at",
        "updated_at",
    },
    "usage_ledger": {
        "id",
        "provider",
        "model",
        "region",
        "operation_id",
        "unit",
        "measured_units",
        "rate_card_id",
        "actual_usd_micros",
        "fx_rate",
        "actual_vnd",
        "billing_confidence",
        "created_at",
    },
    "exports": {
        "id",
        "chapter_id",
        "kind",
        "status",
        "bundle_artifact_id",
        "manifest_sha256",
        "rights_evaluation_json",
        "created_at",
        "revoked_at",
    },
    "audit_events": {
        "id",
        "actor",
        "action",
        "entity_type",
        "entity_id",
        "before_hash",
        "after_hash",
        "redacted_details",
        "created_at",
    },
    "event_log": {
        "sequence_id",
        "entity_type",
        "entity_id",
        "created_at",
    },
}


LOWERCASE_HASH = "a" * 64


def test_contract_enums_match_locked_schema() -> None:
    for enum_type, expected_names in EXPECTED_ENUMS.items():
        assert {member.name for member in enum_type} == expected_names


def test_revision_0001_is_frozen_and_does_not_import_live_models() -> None:
    backend_root = Path(__file__).parents[2]
    source = (backend_root / "migrations" / "versions" / "0001_canonical_schema.py").read_text(encoding="utf-8")

    assert "app.db" not in source
    assert "Base" not in source
    assert "create_all" not in source
    assert "drop_all" not in source


def test_artifact_store_fixture_is_task3_temp_root(artifact_store, tmp_path) -> None:
    assert artifact_store.is_dir()
    assert artifact_store == tmp_path / "artifacts"
    assert artifact_store.resolve().is_relative_to(tmp_path.resolve())


def test_migration_has_complete_schema(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    assert EXPECTED_TABLES <= set(inspector.get_table_names())
    with migrated_engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000


def test_schema_columns_match_canonical_contract(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    for table_name, expected_columns in EXPECTED_COLUMNS.items():
        actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
        assert expected_columns <= actual_columns


def test_required_indexes_and_unique_constraints_exist(migrated_engine) -> None:
    inspector = inspect(migrated_engine)

    indexes = {
        table_name: {
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table_name)
        }
        for table_name in EXPECTED_TABLES
    }
    unique_constraints = {
        table_name: {
            (constraint["name"], tuple(constraint["column_names"]))
            for constraint in inspector.get_unique_constraints(table_name)
        }
        for table_name in EXPECTED_TABLES
    }

    assert ("ix_chapters_project_state_ordinal", ("project_id", "state", "ordinal"), False) in indexes["chapters"]
    assert ("ix_jobs_status_next_priority", ("status", "next_run_at", "priority"), False) in indexes["jobs"]
    assert ("ix_jobs_lease_expires_at", ("lease_expires_at",), False) in indexes["jobs"]
    assert (
        "ix_artifacts_kind_input_settings_status",
        ("kind", "input_hash", "settings_hash", "status"),
        False,
    ) in indexes["artifacts"]
    assert ("uq_ready_artifact_cache", ("kind", "input_hash", "settings_hash"), True) in indexes["artifacts"]
    assert ("uq_ready_export_manifest", ("chapter_id", "kind", "manifest_sha256"), True) in indexes["exports"]
    assert ("ix_qa_issues_chapter_status_severity", ("chapter_id", "status", "severity"), False) in indexes["qa_issues"]
    assert ("ix_usage_ledger_created_provider_model", ("created_at", "provider", "model"), False) in indexes[
        "usage_ledger"
    ]

    assert ("uq_projects_slug", ("slug",)) in unique_constraints["projects"]
    assert (
        "uq_rights_grants_project_scope_territory_valid_from",
        ("project_id", "scope", "territory", "valid_from"),
    ) in unique_constraints["rights_grants"]
    assert ("uq_chapters_project_ordinal", ("project_id", "ordinal")) in unique_constraints["chapters"]
    assert ("uq_source_revisions_chapter_revision_no", ("chapter_id", "revision_no")) in unique_constraints[
        "source_revisions"
    ]
    assert ("uq_source_revisions_chapter_normalized_sha256", ("chapter_id", "normalized_sha256")) in unique_constraints[
        "source_revisions"
    ]
    assert ("uq_source_segments_revision_segment_index", ("source_revision_id", "segment_index")) in unique_constraints[
        "source_segments"
    ]
    assert (
        "uq_translation_segments_run_source_segment",
        ("translation_run_id", "source_segment_id"),
    ) in unique_constraints["translation_segments"]
    assert ("uq_voice_plans_chapter_revision_no", ("chapter_id", "revision_no")) in unique_constraints["voice_plans"]
    assert ("uq_speech_segments_plan_segment_index", ("voice_plan_id", "segment_index")) in unique_constraints[
        "speech_segments"
    ]
    assert ("uq_jobs_idempotency_key", ("idempotency_key",)) in unique_constraints["jobs"]
    assert ("uq_job_attempts_job_attempt_no", ("job_id", "attempt_no")) in unique_constraints["job_attempts"]


def test_ready_artifact_cache_index_is_partial_for_ready_status(migrated_engine) -> None:
    with migrated_engine.connect() as connection:
        sql = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'uq_ready_artifact_cache'")
        ).scalar_one()

    assert "UNIQUE INDEX uq_ready_artifact_cache" in sql
    assert "kind, input_hash, settings_hash" in sql
    assert "WHERE status = 'READY'" in sql


def test_ready_export_manifest_index_is_partial_for_ready_status(migrated_engine) -> None:
    with migrated_engine.connect() as connection:
        sql = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'uq_ready_export_manifest'")
        ).scalar_one()

    assert "UNIQUE INDEX uq_ready_export_manifest" in sql
    assert "chapter_id, kind, manifest_sha256" in sql
    assert "WHERE status = 'READY'" in sql


def test_sqlite_enforces_foreign_keys_and_check_constraints(migrated_engine) -> None:
    with migrated_engine.begin() as connection:
        with pytest.raises(Exception, match="FOREIGN KEY constraint failed"):
            connection.exec_driver_sql(
                """
                INSERT INTO chapters (id, project_id, ordinal, state, created_at, updated_at)
                VALUES ('018f0000-0000-7000-8000-000000000001',
                        '018f0000-0000-7000-8000-000000000099',
                        1,
                        'IMPORTED',
                        '2026-08-19T00:00:00+00:00',
                        '2026-08-19T00:00:00+00:00')
                """
            )

        with pytest.raises(Exception, match="CHECK constraint failed"):
            connection.exec_driver_sql(
                """
                INSERT INTO projects
                (id, title, slug, source_type, rights_status, created_at, updated_at)
                VALUES ('018f0000-0000-7000-8000-000000000001',
                        'Title',
                        'title',
                        'QQ_FETCH',
                        'PRIVATE_ONLY',
                        '2026-08-19T00:00:00+00:00',
                        '2026-08-19T00:00:00+00:00')
                """
        )


@pytest.mark.parametrize("bad_hash", ["A" * 64, "a" * 63, ("g" * 64)])
def test_artifact_hash_constraints_reject_invalid_lowercase_sha256_values(migrated_engine, bad_hash) -> None:
    valid_hash = LOWERCASE_HASH
    cases = [
        ("sha256", bad_hash, valid_hash, valid_hash),
        ("input_hash", valid_hash, bad_hash, valid_hash),
        ("settings_hash", valid_hash, valid_hash, bad_hash),
    ]

    with migrated_engine.begin() as connection:
        for column_name, sha256, input_hash, settings_hash in cases:
            with pytest.raises(Exception, match="CHECK constraint failed"):
                connection.execute(
                    text(
                        """
                        INSERT INTO artifacts
                        (id, kind, status, relative_path, sha256, byte_size, mime_type,
                         input_hash, settings_hash, created_at, updated_at)
                        VALUES
                        (:id, 'REPORT', 'READY', :relative_path, :sha256, 1, 'application/json',
                         :input_hash, :settings_hash,
                         '2026-08-19T00:00:00+00:00', '2026-08-19T00:00:00+00:00')
                        """
                    ),
                    {
                        "id": f"018f0000-0000-7000-8000-{len(column_name):012x}",
                        "relative_path": f"{column_name}.json",
                        "sha256": sha256,
                        "input_hash": input_hash,
                        "settings_hash": settings_hash,
                    },
                )


def test_artifact_hash_constraints_accept_valid_lowercase_hashes(migrated_engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO artifacts
                (id, kind, status, relative_path, sha256, byte_size, mime_type,
                 input_hash, settings_hash, created_at, updated_at)
                VALUES
                ('018f0000-0000-7000-8000-000000000501',
                 'REPORT',
                 'READY',
                 'reports/valid.json',
                 :sha256,
                 1,
                 'application/json',
                 :input_hash,
                 :settings_hash,
                 '2026-08-19T00:00:00+00:00',
                 '2026-08-19T00:00:00+00:00')
                """
            ),
            {"sha256": LOWERCASE_HASH, "input_hash": "b" * 64, "settings_hash": "c" * 64},
        )

        assert connection.execute(text("SELECT COUNT(*) FROM artifacts")).scalar_one() == 1


def test_all_canonical_hash_fields_have_nullable_aware_checks(migrated_engine) -> None:
    expected_hash_columns = {
        "rights_evidence": {"sha256"},
        "source_revisions": {"normalized_sha256"},
        "source_segments": {"source_sha256"},
        "translation_runs": {"glossary_revision_hash", "story_memory_revision_hash", "translation_text_sha256"},
        "translation_segments": {"target_sha256"},
        "voice_presets": {"model_snapshot_hash"},
        "voice_plans": {"plan_sha256"},
        "speech_segments": {"narration_sha256", "pronunciation_revision_hash"},
        "artifacts": {"sha256", "input_hash", "settings_hash"},
        "exports": {"manifest_sha256"},
        "audit_events": {"before_hash", "after_hash"},
    }

    with migrated_engine.connect() as connection:
        for table_name, column_names in expected_hash_columns.items():
            create_sql = connection.execute(
                text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :table_name"),
                {"table_name": table_name},
            ).scalar_one()
            for column_name in column_names:
                assert f"{column_name}_lowercase_sha256" in create_sql
                assert f"{column_name} IS NULL OR" in create_sql
                assert f"length({column_name}) = 64" in create_sql
                assert f"{column_name} NOT GLOB '*[^0-9a-f]*'" in create_sql


def test_frozen_migration_matches_orm_metadata_server_defaults(migrated_engine) -> None:
    from app.db import models  # noqa: F401
    from app.db.base import Base

    with migrated_engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_server_default": True,
                "target_metadata": Base.metadata,
            },
        )
        diffs = compare_metadata(context, Base.metadata)

    assert diffs == []


def test_translation_runs_created_by_has_database_default(migrated_engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects (id, title, slug, source_type, rights_status, created_at, updated_at)
                VALUES
                ('018f0000-0000-7000-8000-000000000601', 'Title', 'created-by-title',
                 'SELF_AUTHORED', 'PRIVATE_ONLY',
                 '2026-08-19T00:00:00+00:00', '2026-08-19T00:00:00+00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO chapters (id, project_id, ordinal, state, created_at, updated_at)
                VALUES
                ('018f0000-0000-7000-8000-000000000602',
                 '018f0000-0000-7000-8000-000000000601',
                 1,
                 'IMPORTED',
                 '2026-08-19T00:00:00+00:00',
                 '2026-08-19T00:00:00+00:00')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO source_revisions
                (id, chapter_id, revision_no, import_kind, normalized_text, normalized_sha256,
                 han_char_count, total_char_count, normalizer_version, created_at, updated_at)
                VALUES
                ('018f0000-0000-7000-8000-000000000603',
                 '018f0000-0000-7000-8000-000000000602',
                 1,
                 'PASTE',
                 'text',
                 :hash_value,
                 0,
                 4,
                 'v1',
                 '2026-08-19T00:00:00+00:00',
                 '2026-08-19T00:00:00+00:00')
                """
            ),
            {"hash_value": LOWERCASE_HASH},
        )
        connection.execute(
            text(
                """
                INSERT INTO translation_runs
                (id, chapter_id, source_revision_id, prompt_version, status, created_at, updated_at)
                VALUES
                ('018f0000-0000-7000-8000-000000000604',
                 '018f0000-0000-7000-8000-000000000602',
                 '018f0000-0000-7000-8000-000000000603',
                 'v1',
                 'PENDING',
                 '2026-08-19T00:00:00+00:00',
                 '2026-08-19T00:00:00+00:00')
                """
            )
        )

        created_by = connection.execute(text("SELECT created_by FROM translation_runs")).scalar_one()

    assert created_by == "LOCAL_OWNER"


def test_downgrade_upgrade_roundtrip_on_temp_database(tmp_path) -> None:
    from app.db.base import create_engine_for

    backend_root = Path(__file__).parents[2]
    db_path = tmp_path / "roundtrip.sqlite3"
    engine = create_engine_for(db_path)
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")

    command.upgrade(config, "head")
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())

    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}

    command.upgrade(config, "head")
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_datetime_type_rejects_naive_and_serializes_aware(db_session) -> None:
    from app.db.models import Project

    project = Project(
        id="018f0000-0000-7000-8000-000000000001",
        title="Title",
        slug="title",
        source_type=SourceType.SELF_AUTHORED.name,
        rights_status=RightsStatus.PRIVATE_ONLY.name,
        created_at=datetime(2026, 8, 19, 1, 2, 3, tzinfo=UTC),
        updated_at=datetime(2026, 8, 19, 1, 2, 3, tzinfo=UTC),
    )
    db_session.add(project)
    db_session.commit()

    stored = db_session.execute(text("SELECT created_at FROM projects WHERE id = :id"), {"id": project.id}).scalar_one()
    assert stored == "2026-08-19T01:02:03+00:00"

    db_session.add(
        Project(
            id="018f0000-0000-7000-8000-000000000002",
            title="Naive",
            slug="naive",
            source_type=SourceType.SELF_AUTHORED.name,
            rights_status=RightsStatus.PRIVATE_ONLY.name,
            created_at=datetime(2026, 8, 19, 1, 2, 3),
            updated_at=datetime(2026, 8, 19, 1, 2, 3, tzinfo=UTC),
        )
    )

    with pytest.raises(StatementError, match="timezone-aware"):
        db_session.commit()
