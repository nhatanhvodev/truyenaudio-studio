from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    CloudConsentStatus,
    ProviderKind,
    RightsScope,
    RightsStatus,
    SourceType,
    Usage,
    UsageUnit,
)
from app.db.models import Artifact, CloudProcessingConsent, Project, ProviderProfile, RateCard, RightsGrant
from app.modules.budgets.guard import BudgetGuard
from app.modules.compliance.cloud import CloudCallGuard


NOW = datetime(2026, 8, 19, 8, 0, 0, tzinfo=UTC)
PROJECT_ID = "018f0000-0000-7000-8000-000000003001"
PROFILE_ID = "018f0000-0000-7000-8000-000000003002"
CONSENT_ID = "018f0000-0000-7000-8000-000000003003"
POLICY_ARTIFACT_ID = "018f0000-0000-7000-8000-000000003004"
POLICY_HASH = "a" * 64


@dataclass(frozen=True)
class CloudCase:
    request: dict[str, object]
    consent: CloudProcessingConsent
    profile: ProviderProfile


@pytest.fixture
def cloud_guard(db_session) -> CloudCallGuard:
    return CloudCallGuard(db_session, BudgetGuard(db_session, now=lambda: NOW), now=lambda: NOW)


@pytest.fixture
def qwen_case(db_session) -> CloudCase:
    _seed_valid_qwen_case(db_session)
    consent = db_session.get(CloudProcessingConsent, CONSENT_ID)
    profile = db_session.get(ProviderProfile, PROFILE_ID)
    assert consent is not None
    assert profile is not None
    return CloudCase(
        request={
            "project_id": PROJECT_ID,
            "provider_profile_id": PROFILE_ID,
            "operation_id": "translate-segment-001",
            "estimated_usage": (Usage(UsageUnit.INPUT_TOKEN.value, 10_000),),
            "category": "REGULAR",
        },
        consent=consent,
        profile=profile,
    )


def test_qwen_cloud_call_requires_provider_policy_consent(cloud_guard, qwen_case, db_session) -> None:
    qwen_case.consent.policy_snapshot_artifact_id = None
    db_session.commit()

    decision = cloud_guard.evaluate(**qwen_case.request)

    assert not decision.allowed
    assert "POLICY_SNAPSHOT_REQUIRED" in decision.reasons
    assert decision.authorization_id is None


def test_qwen_cloud_call_rejects_stale_policy_hash(cloud_guard, qwen_case, db_session) -> None:
    qwen_case.profile.config_json = {"provider": "qwen", "policy_sha256": "b" * 64}
    db_session.commit()

    decision = cloud_guard.evaluate(**qwen_case.request)

    assert not decision.allowed
    assert "POLICY_SNAPSHOT_STALE" in decision.reasons
    assert decision.authorization_id is None


def test_qwen_cloud_call_requires_ai_and_third_party_cloud_grant(cloud_guard, qwen_case, db_session) -> None:
    db_session.query(RightsGrant).delete()
    db_session.commit()

    decision = cloud_guard.evaluate(**qwen_case.request)

    assert not decision.allowed
    assert "RIGHTS_CLOUD_NOT_PERMITTED" in decision.reasons
    assert decision.authorization_id is None


def test_qwen_cloud_call_requires_exact_rate_cards(cloud_guard, qwen_case, db_session) -> None:
    db_session.query(RateCard).delete()
    db_session.commit()

    decision = cloud_guard.evaluate(**qwen_case.request)

    assert not decision.allowed
    assert "BUDGET_RATE_MISSING" in decision.reasons
    assert decision.authorization_id is None


def test_revoked_consent_blocks_new_cloud_calls(cloud_guard, qwen_case, db_session) -> None:
    qwen_case.consent.status = CloudConsentStatus.REVOKED.value
    qwen_case.consent.revoked_at = NOW
    db_session.commit()

    decision = cloud_guard.evaluate(**qwen_case.request)

    assert not decision.allowed
    assert "CONSENT_NOT_GRANTED" in decision.reasons
    assert decision.authorization_id is None


def test_valid_cloud_call_returns_current_consent_rate_cards_and_budget_hold(cloud_guard, qwen_case, db_session) -> None:
    rate_card = db_session.query(RateCard).one()

    decision = cloud_guard.evaluate(**qwen_case.request)

    assert decision.allowed
    assert decision.cloud_consent_id == CONSENT_ID
    assert decision.authorization_id is not None
    assert decision.rate_card_ids == (rate_card.id,)
    assert decision.reasons == ()


def _seed_valid_qwen_case(db_session) -> None:
    db_session.add(
        Project(
            id=PROJECT_ID,
            title="Cloud Translation",
            slug="cloud-translation",
            source_type=SourceType.SELF_AUTHORED.value,
            rights_status=RightsStatus.CLEARED.value,
        )
    )
    db_session.add(
        ProviderProfile(
            id=PROFILE_ID,
            provider_kind=ProviderKind.TRANSLATOR.value,
            adapter_name="qwen-mt",
            display_name="Qwen MT Flash",
            model="qwen-mt-flash",
            region="frankfurt",
            secret_ref="env:QWEN_API_KEY",
            config_json={"provider": "qwen", "policy_sha256": POLICY_HASH},
            enabled=True,
        )
    )
    db_session.add(
        Artifact(
            id=POLICY_ARTIFACT_ID,
            kind=ArtifactKind.LICENSE_SNAPSHOT.value,
            status=ArtifactStatus.READY.value,
            relative_path="projects/cloud-translation/qwen-policy.json",
            sha256=POLICY_HASH,
            byte_size=32,
            mime_type="application/json",
            input_hash="1" * 64,
            settings_hash="2" * 64,
            producer="CloudConsentService",
            producer_version="task3",
        )
    )
    db_session.flush()
    db_session.add(
        CloudProcessingConsent(
            id=CONSENT_ID,
            project_id=PROJECT_ID,
            provider_profile_id=PROFILE_ID,
            status=CloudConsentStatus.GRANTED.value,
            policy_snapshot_artifact_id=POLICY_ARTIFACT_ID,
            accepted_at=NOW,
        )
    )
    db_session.add(
        RightsGrant(
            id="018f0000-0000-7000-8000-000000003005",
            project_id=PROJECT_ID,
            scope=RightsScope.TRANSLATE_VI.value,
            territory="WORLD",
            allows_ai_processing=True,
            allows_third_party_cloud=True,
            valid_from=NOW - timedelta(days=1),
        )
    )
    db_session.add(
        RateCard(
            id="018f0000-0000-7000-8000-000000003006",
            provider="qwen",
            model="qwen-mt-flash",
            region="frankfurt",
            unit=UsageUnit.INPUT_TOKEN.value,
            price_usd_micros_per_million_units=1_000_000,
            effective_from=NOW - timedelta(days=1),
            source_url="https://example.test/qwen-rate-card",
            source_note="fixture",
            verified_at=NOW,
        )
    )
    db_session.commit()
