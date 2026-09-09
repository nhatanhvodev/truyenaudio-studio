from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.contracts import ArtifactKind, ArtifactStatus, CloudConsentStatus, ExportKind, ProviderKind, RightsScope, RightsStatus, SourceType, Usage, UsageUnit
from app.db.models import Artifact, BudgetAuthorization, Chapter, CloudProcessingConsent, Project, ProviderProfile, RateCard, RightsEvidence, RightsGrant
from app.modules.budgets.guard import BudgetGuard
from app.modules.compliance.cloud import CloudCallGuard
from app.modules.exports.workflow import ExportWorkflow


NOW = datetime(2026, 8, 19, 8, 0, 0, tzinfo=UTC)
PROJECT_ID = "018f0000-0000-7000-8000-000000013001"
CHAPTER_ID = "018f0000-0000-7000-8000-000000013002"
PROFILE_ID = "018f0000-0000-7000-8000-000000013003"
CONSENT_ID = "018f0000-0000-7000-8000-000000013004"
POLICY_ID = "018f0000-0000-7000-8000-000000013005"
RATE_ID = "018f0000-0000-7000-8000-000000013006"
AUTH_ID = "018f0000-0000-7000-8000-000000013007"
POLICY_HASH = "f" * 64


@pytest.mark.parametrize("missing", ["consent", "policy", "rate_card", "quota", "authorization"])
def test_cloud_call_fails_closed(db_session, missing: str) -> None:
    _seed_valid_tts_case(db_session)
    if missing == "consent":
        db_session.get(CloudProcessingConsent, CONSENT_ID).status = CloudConsentStatus.REVOKED.value
    if missing == "policy":
        db_session.get(CloudProcessingConsent, CONSENT_ID).policy_snapshot_artifact_id = None
    if missing == "rate_card":
        db_session.query(RateCard).delete()
    if missing == "quota":
        db_session.get(ProviderProfile, PROFILE_ID).config_json = {"provider": "google", "policy_sha256": POLICY_HASH}
    if missing == "authorization":
        db_session.query(BudgetAuthorization).delete()
    db_session.commit()
    guard = CloudCallGuard(db_session, BudgetGuard(db_session, now=lambda: NOW), now=lambda: NOW)

    decision = guard.evaluate(
        project_id=PROJECT_ID,
        provider_profile_id=PROFILE_ID,
        operation_id="tts-segment-001",
        estimated_usage=(Usage(UsageUnit.CHARACTER.value, 1_500),),
        category="REGULAR",
        cloud_consent_id=CONSENT_ID,
        budget_authorization_id=AUTH_ID,
    )

    assert not decision.allowed
    assert decision.authorization_id is None


def test_cloud_call_reports_remaining_quota_and_authorizes_billable_overage(db_session) -> None:
    _seed_valid_tts_case(db_session)
    guard = CloudCallGuard(db_session, BudgetGuard(db_session, now=lambda: NOW), now=lambda: NOW)
    auth_id = _budget_authorization_id(db_session, "tts-segment-001")

    decision = guard.evaluate(
        project_id=PROJECT_ID,
        provider_profile_id=PROFILE_ID,
        operation_id="tts-segment-001",
        estimated_usage=(Usage(UsageUnit.CHARACTER.value, 1_500),),
        category="REGULAR",
        cloud_consent_id=CONSENT_ID,
        budget_authorization_id=auth_id,
    )

    assert decision.allowed
    assert decision.authorization_id == auth_id
    assert decision.rate_card_ids == (RATE_ID,)
    assert decision.remaining_quota == (Usage(UsageUnit.CHARACTER.value, 1_000, "quota:2026-08"),)


def test_tts_cloud_call_requires_explicit_budget_authorization(db_session) -> None:
    _seed_valid_tts_case(db_session)
    guard = CloudCallGuard(db_session, BudgetGuard(db_session, now=lambda: NOW), now=lambda: NOW)

    decision = guard.evaluate(
        project_id=PROJECT_ID,
        provider_profile_id=PROFILE_ID,
        operation_id="tts-segment-001",
        estimated_usage=(Usage(UsageUnit.CHARACTER.value, 1_500),),
        category="REGULAR",
        cloud_consent_id=CONSENT_ID,
    )

    assert not decision.allowed
    assert decision.reasons == ("BUDGET_AUTHORIZATION_REQUIRED",)


def test_private_attestation_allows_processing_not_publication(db_session, tmp_path) -> None:
    _seed_valid_tts_case(db_session, source_type=SourceType.USER_SUPPLIED_PRIVATE)
    guard = CloudCallGuard(db_session, BudgetGuard(db_session, now=lambda: NOW), now=lambda: NOW)
    auth_id = _budget_authorization_id(db_session, "tts-private-001")

    decision = guard.evaluate(
        project_id=PROJECT_ID,
        provider_profile_id=PROFILE_ID,
        operation_id="tts-private-001",
        estimated_usage=(Usage(UsageUnit.CHARACTER.value, 200),),
        category="REGULAR",
        cloud_consent_id=CONSENT_ID,
        budget_authorization_id=auth_id,
    )

    assert decision.allowed
    export_gate = ExportWorkflow(db_session, artifact_root=tmp_path, now=lambda: NOW)
    assert not export_gate.evaluate_gate(CHAPTER_ID, ExportKind.PUBLICATION_BUNDLE).allowed


def _seed_valid_tts_case(db_session, *, source_type: SourceType = SourceType.SELF_AUTHORED) -> None:
    db_session.add(
        Project(
            id=PROJECT_ID,
            title="Cloud TTS",
            slug="cloud-tts",
            source_type=source_type.value,
            rights_status=RightsStatus.PRIVATE_ONLY.value,
        )
    )
    db_session.flush()
    db_session.add(
        Chapter(
            id=CHAPTER_ID,
            project_id=PROJECT_ID,
            ordinal=1,
            state="TRANSLATION_APPROVED",
        )
    )
    db_session.add(
        ProviderProfile(
            id=PROFILE_ID,
            provider_kind=ProviderKind.TTS.value,
            adapter_name="google-tts",
            display_name="Google Neural2",
            model="neural2",
            region="asia-southeast1",
            secret_ref="keyring:google-tts",
            config_json={
                "provider": "google",
                "policy_sha256": POLICY_HASH,
                "quota": [{"unit": UsageUnit.CHARACTER.value, "month": "2026-08", "configured_units": 1_000}],
            },
            enabled=True,
        )
    )
    db_session.add(
        Artifact(
            id=POLICY_ID,
            kind=ArtifactKind.LICENSE_SNAPSHOT.value,
            status=ArtifactStatus.READY.value,
            relative_path="providers/google-policy.json",
            sha256=POLICY_HASH,
            byte_size=32,
            mime_type="application/json",
            input_hash="1" * 64,
            settings_hash="2" * 64,
        )
    )
    db_session.flush()
    evidence_id = "018f0000-0000-7000-8000-000000013008"
    db_session.add(
        RightsEvidence(
            id=evidence_id,
            project_id=PROJECT_ID,
            evidence_kind="USER_ATTESTATION",
            display_name="private cloud attestation",
            relative_path="evidence/private.txt",
            sha256="3" * 64,
            notes="private processing only",
        )
    )
    db_session.flush()
    db_session.add(
        CloudProcessingConsent(
            id=CONSENT_ID,
            project_id=PROJECT_ID,
            provider_profile_id=PROFILE_ID,
            status=CloudConsentStatus.GRANTED.value,
            attestation_evidence_id=evidence_id,
            policy_snapshot_artifact_id=POLICY_ID,
            accepted_at=NOW,
        )
    )
    db_session.add(
        RateCard(
            id=RATE_ID,
            provider="google",
            model="neural2",
            region="asia-southeast1",
            unit=UsageUnit.CHARACTER.value,
            price_usd_micros_per_million_units=4_000_000,
            effective_from=NOW - timedelta(days=1),
        )
    )
    if source_type is not SourceType.USER_SUPPLIED_PRIVATE:
        db_session.add(
            RightsGrant(
                id="018f0000-0000-7000-8000-000000013010",
                project_id=PROJECT_ID,
                scope=RightsScope.TRANSLATE_VI.value,
                territory="WORLD",
                allows_ai_processing=True,
                allows_third_party_cloud=True,
                valid_from=NOW - timedelta(days=1),
            )
        )
    db_session.commit()
    guard = CloudCallGuard(db_session, BudgetGuard(db_session, now=lambda: NOW), now=lambda: NOW)
    first = guard.reserve(
        project_id=PROJECT_ID,
        provider_profile_id=PROFILE_ID,
        operation_id="tts-segment-001",
        estimated_usage=(Usage(UsageUnit.CHARACTER.value, 1_500),),
        category="REGULAR",
        cloud_consent_id=CONSENT_ID,
    )
    second = guard.reserve(
        project_id=PROJECT_ID,
        provider_profile_id=PROFILE_ID,
        operation_id="tts-private-001",
        estimated_usage=(Usage(UsageUnit.CHARACTER.value, 200),),
        category="REGULAR",
        cloud_consent_id=CONSENT_ID,
    )
    assert first.allowed
    assert second.allowed


def _budget_authorization_id(db_session, operation_id: str) -> str:
    value = db_session.scalar(select(BudgetAuthorization.id).where(BudgetAuthorization.operation_id == operation_id))
    assert value is not None
    return value
