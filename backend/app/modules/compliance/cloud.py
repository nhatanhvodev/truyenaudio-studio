from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import (
    ArtifactStatus,
    CloudConsentStatus,
    RightsScope,
    SourceType,
    Usage,
)
from app.db.models import Artifact, CloudProcessingConsent, Project, ProviderProfile, RightsGrant
from app.modules.budgets.guard import BudgetBlocked, BudgetGuard


@dataclass(frozen=True)
class CloudCallDecision:
    allowed: bool
    cloud_consent_id: str | None
    authorization_id: str | None
    rate_card_ids: tuple[str, ...]
    remaining_quota: tuple[Usage, ...]
    reasons: tuple[str, ...]


class CloudCallBlocked(Exception):
    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__(", ".join(reasons))
        self.reasons = reasons


class CloudCallGuard:
    def __init__(self, session: Session, budget_guard: BudgetGuard, *, now: Callable[[], datetime] | None = None) -> None:
        self.session = session
        self.budget_guard = budget_guard
        self._now = now

    def evaluate(
        self,
        project_id: str,
        provider_profile_id: str,
        operation_id: str,
        estimated_usage: tuple[Usage, ...],
        category: str,
        *,
        cloud_consent_id: str | None = None,
        budget_authorization_id: str | None = None,
    ) -> CloudCallDecision:
        profile = self.session.get(ProviderProfile, provider_profile_id)
        if profile is None or not profile.enabled:
            return _deny("PROVIDER_PROFILE_DISABLED")

        project = self.session.get(Project, project_id)
        if project is None:
            return _deny("PROJECT_NOT_FOUND")

        consent = self._granted_consent(project_id, provider_profile_id, cloud_consent_id)
        if consent is None:
            return _deny("CONSENT_NOT_GRANTED")

        policy = self._policy_artifact(consent)
        if policy is None:
            return _deny("POLICY_SNAPSHOT_REQUIRED")
        if policy.status != ArtifactStatus.READY.value:
            return _deny("POLICY_SNAPSHOT_NOT_READY")
        current_hash = _profile_config(profile).get("policy_sha256")
        if current_hash and policy.sha256 != current_hash:
            return _deny("POLICY_SNAPSHOT_STALE")

        if not self._rights_allow_cloud_translation(project):
            return _deny("RIGHTS_CLOUD_NOT_PERMITTED")

        provider = _provider_name(profile)
        try:
            quote = self.budget_guard.quote_usage(
                operation_id=operation_id,
                provider=provider,
                model=profile.model or "",
                region=profile.region,
                usage=estimated_usage,
                category=category,
            )
            if budget_authorization_id is None:
                authorization = self.budget_guard.authorize(quote)
            else:
                authorization = self.budget_guard.validate_authorization(budget_authorization_id, operation_id)
        except BudgetBlocked as exc:
            return _deny(exc.reason)

        return CloudCallDecision(
            allowed=True,
            cloud_consent_id=consent.id,
            authorization_id=authorization.id,
            rate_card_ids=quote.rate_card_ids,
            remaining_quota=(),
            reasons=(),
        )

    def now(self) -> datetime:
        if self._now is not None:
            return self._now()
        from app.db.base import utc_now

        return utc_now()

    def _granted_consent(
        self,
        project_id: str,
        provider_profile_id: str,
        cloud_consent_id: str | None,
    ) -> CloudProcessingConsent | None:
        statement = select(CloudProcessingConsent).where(
            CloudProcessingConsent.project_id == project_id,
            CloudProcessingConsent.provider_profile_id == provider_profile_id,
            CloudProcessingConsent.status == CloudConsentStatus.GRANTED.value,
        )
        if cloud_consent_id is not None:
            statement = statement.where(CloudProcessingConsent.id == cloud_consent_id)
        return self.session.scalar(statement.order_by(CloudProcessingConsent.accepted_at.desc()))

    def _policy_artifact(self, consent: CloudProcessingConsent) -> Artifact | None:
        if consent.policy_snapshot_artifact_id is None:
            return None
        return self.session.get(Artifact, consent.policy_snapshot_artifact_id)

    def _rights_allow_cloud_translation(self, project: Project) -> bool:
        if project.source_type == SourceType.USER_SUPPLIED_PRIVATE.value:
            consent = self.session.scalar(
                select(CloudProcessingConsent).where(
                    CloudProcessingConsent.project_id == project.id,
                    CloudProcessingConsent.status == CloudConsentStatus.GRANTED.value,
                    CloudProcessingConsent.attestation_evidence_id.is_not(None),
                )
            )
            if consent is not None:
                return True

        grants = self.session.scalars(
            select(RightsGrant).where(
                RightsGrant.project_id == project.id,
                RightsGrant.scope == RightsScope.TRANSLATE_VI.value,
                RightsGrant.allows_ai_processing.is_(True),
                RightsGrant.allows_third_party_cloud.is_(True),
                RightsGrant.valid_from <= self.now(),
                (RightsGrant.expires_at.is_(None) | (RightsGrant.expires_at > self.now())),
            )
        ).all()
        return bool(grants)


def _deny(reason: str) -> CloudCallDecision:
    return CloudCallDecision(
        allowed=False,
        cloud_consent_id=None,
        authorization_id=None,
        rate_card_ids=(),
        remaining_quota=(),
        reasons=(reason,),
    )


def _provider_name(profile: ProviderProfile) -> str:
    return str(_profile_config(profile).get("provider") or profile.adapter_name)


def _profile_config(profile: ProviderProfile) -> dict[str, object]:
    return profile.config_json or {}
