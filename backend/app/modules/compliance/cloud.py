from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import (
    ArtifactStatus,
    CloudConsentStatus,
    ProviderKind,
    RightsScope,
    SourceType,
    Usage,
)
from app.db.models import Artifact, CloudProcessingConsent, Project, ProviderProfile, RightsGrant
from app.modules.budgets.guard import BudgetBlocked, BudgetGuard, CostQuote
from app.modules.budgets.quota import QuotaUnavailable, evaluate_profile_quota


@dataclass(frozen=True)
class CloudCallDecision:
    allowed: bool
    cloud_consent_id: str | None
    authorization_id: str | None
    rate_card_ids: tuple[str, ...]
    remaining_quota: tuple[Usage, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _PreparedQuote:
    quote: CostQuote
    cloud_consent_id: str
    rate_card_ids: tuple[str, ...]
    remaining_quota: tuple[Usage, ...]
    warnings: tuple[str, ...]


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
        stage: str | None = None,
    ) -> CloudCallDecision:
        prepared = self._prepare_quote(
            project_id=project_id,
            provider_profile_id=provider_profile_id,
            operation_id=operation_id,
            estimated_usage=estimated_usage,
            category=category,
            cloud_consent_id=cloud_consent_id,
            stage=stage,
        )
        if isinstance(prepared, CloudCallDecision):
            return prepared
        if budget_authorization_id is None:
            return _deny("BUDGET_AUTHORIZATION_REQUIRED")
        try:
            authorization = self.budget_guard.validate_authorization_for_quote(budget_authorization_id, prepared.quote)
        except BudgetBlocked as exc:
            return _deny(exc.reason)

        return CloudCallDecision(
            allowed=True,
            cloud_consent_id=prepared.cloud_consent_id,
            authorization_id=authorization.id,
            rate_card_ids=prepared.rate_card_ids,
            remaining_quota=prepared.remaining_quota,
            reasons=(),
            warnings=prepared.warnings,
        )

    def reserve(
        self,
        project_id: str,
        provider_profile_id: str,
        operation_id: str,
        estimated_usage: tuple[Usage, ...],
        category: str,
        *,
        cloud_consent_id: str | None = None,
        stage: str | None = None,
    ) -> CloudCallDecision:
        prepared = self._prepare_quote(
            project_id=project_id,
            provider_profile_id=provider_profile_id,
            operation_id=operation_id,
            estimated_usage=estimated_usage,
            category=category,
            cloud_consent_id=cloud_consent_id,
            stage=stage,
        )
        if isinstance(prepared, CloudCallDecision):
            return prepared
        try:
            authorization = self.budget_guard.authorize(prepared.quote)
        except BudgetBlocked as exc:
            return _deny(exc.reason)

        return CloudCallDecision(
            allowed=True,
            cloud_consent_id=prepared.cloud_consent_id,
            authorization_id=authorization.id,
            rate_card_ids=prepared.rate_card_ids,
            remaining_quota=prepared.remaining_quota,
            reasons=(),
            warnings=prepared.warnings,
        )

    def _prepare_quote(
        self,
        project_id: str,
        provider_profile_id: str,
        operation_id: str,
        estimated_usage: tuple[Usage, ...],
        category: str,
        *,
        cloud_consent_id: str | None = None,
        stage: str | None = None,
    ) -> _PreparedQuote | CloudCallDecision:
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

        if not self._rights_allow_cloud_translation(project, consent):
            return _deny("RIGHTS_CLOUD_NOT_PERMITTED")

        provider = _provider_name(profile)
        try:
            quota = evaluate_profile_quota(
                self.session,
                profile,
                estimated_usage,
                provider=provider,
                now=self.now(),
                require_config=profile.provider_kind == ProviderKind.TTS.value,
            )
            plan_hash = _plan_hash(
                project_id=project_id,
                provider_profile_id=provider_profile_id,
                provider_profile_revision=profile.revision,
                operation_id=operation_id,
                estimated_usage=estimated_usage,
                category=category,
                cloud_consent_id=consent.id,
                stage=stage or category,
            )
            quote = self.budget_guard.quote_usage(
                operation_id=operation_id,
                provider=provider,
                model=profile.model or "",
                region=profile.region,
                usage=quota.billable_usage,
                category=category,
                provider_profile_id=provider_profile_id,
                provider_profile_revision=profile.revision,
                cloud_consent_id=consent.id,
                stage=stage or category,
                plan_hash=plan_hash,
            )
        except QuotaUnavailable as exc:
            return _deny(str(exc))
        except BudgetBlocked as exc:
            return _deny(exc.reason)

        return _PreparedQuote(
            quote=quote,
            cloud_consent_id=consent.id,
            rate_card_ids=quote.rate_card_ids,
            remaining_quota=quota.remaining_quota,
            warnings=quote.warnings,
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

    def _rights_allow_cloud_translation(self, project: Project, consent: CloudProcessingConsent) -> bool:
        if project.source_type == SourceType.USER_SUPPLIED_PRIVATE.value:
            if consent.attestation_evidence_id is not None:
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


def _plan_hash(
    *,
    project_id: str,
    provider_profile_id: str,
    provider_profile_revision: int,
    operation_id: str,
    estimated_usage: tuple[Usage, ...],
    category: str,
    cloud_consent_id: str,
    stage: str,
) -> str:
    payload = {
        "category": category,
        "cloud_consent_id": cloud_consent_id,
        "estimated_usage": [
            {"unit": item.unit, "measured_units": item.measured_units}
            for item in estimated_usage
        ],
        "operation_id": operation_id,
        "profile_id": provider_profile_id,
        "profile_revision": provider_profile_revision,
        "project_id": project_id,
        "stage": stage,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
