from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import ExportKind, RightsScope, RightsStatus
from app.db.models import Chapter, Project, RightsEvidence, RightsGrant
from app.modules.exports.schemas import GateDecision, PublicationMetadata


PUBLICATION_BASE_SCOPES = (
    RightsScope.TRANSLATE_VI,
    RightsScope.CREATE_AUDIO,
    RightsScope.PUBLIC_STREAM,
)


@dataclass(frozen=True)
class RightsEvaluation:
    decision: GateDecision
    payload: dict[str, object]


class RightsGate:
    def __init__(self, session: Session, *, now: Callable[[], datetime] | None = None) -> None:
        self.session = session
        self._now = now or (lambda: datetime.now(UTC))

    def evaluate(
        self,
        chapter_id: str,
        kind: ExportKind,
        *,
        territory: str = "VN",
        metadata: PublicationMetadata | None = None,
        include_download: bool = False,
    ) -> RightsEvaluation:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError("CHAPTER_NOT_FOUND")
        project = self.session.get(Project, chapter.project_id)
        if project is None:
            raise ValueError("PROJECT_NOT_FOUND")
        if kind is ExportKind.PRIVATE_ARCHIVE:
            payload = {
                "kind": kind.value,
                "project_id": project.id,
                "chapter_id": chapter.id,
                "rights_status": project.rights_status,
                "required_scopes": [],
                "active_grants": [],
                "reasons": [],
            }
            return _evaluation(payload)

        required_scopes = list(PUBLICATION_BASE_SCOPES)
        if include_download:
            required_scopes.append(RightsScope.DOWNLOAD)
        if metadata is not None and metadata.is_premium:
            required_scopes.append(RightsScope.MONETIZE)

        reasons: list[str] = []
        if project.rights_status != RightsStatus.CLEARED.value:
            reasons.append("RIGHTS_NOT_CLEARED")

        active_grants = self._active_grants(project.id, territory)
        active_scopes = {RightsScope(grant.scope) for grant in active_grants}
        for scope in required_scopes:
            if scope not in active_scopes:
                reasons.append(scope.value)

        evidence = self._evidence(active_grants)
        payload = {
            "kind": kind.value,
            "project_id": project.id,
            "chapter_id": chapter.id,
            "rights_status": project.rights_status,
            "territory": territory,
            "required_scopes": [scope.value for scope in required_scopes],
            "active_grants": [
                {
                    "scope": grant.scope,
                    "territory": grant.territory,
                    "valid_from": _iso(grant.valid_from),
                    "expires_at": _iso(grant.expires_at),
                    "evidence_id": grant.evidence_id,
                }
                for grant in active_grants
            ],
            "evidence": [
                {"display_name": item.display_name, "sha256": item.sha256}
                for item in evidence
            ],
            "reasons": reasons,
        }
        return _evaluation(payload)

    def _active_grants(self, project_id: str, territory: str) -> tuple[RightsGrant, ...]:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("RIGHTS_EVALUATION_TIMEZONE_REQUIRED")
        rows = self.session.scalars(
            select(RightsGrant)
            .where(RightsGrant.project_id == project_id)
            .order_by(RightsGrant.scope, RightsGrant.valid_from.desc(), RightsGrant.id)
        ).all()
        accepted = {territory, "GLOBAL", "WORLDWIDE", "ALL"}
        return tuple(
            grant
            for grant in rows
            if grant.territory in accepted
            and grant.valid_from <= now
            and (grant.expires_at is None or grant.expires_at > now)
        )

    def _evidence(self, grants: tuple[RightsGrant, ...]) -> tuple[RightsEvidence, ...]:
        evidence_ids = {grant.evidence_id for grant in grants if grant.evidence_id}
        if not evidence_ids:
            return ()
        rows = self.session.scalars(
            select(RightsEvidence)
            .where(RightsEvidence.id.in_(evidence_ids))
            .order_by(RightsEvidence.display_name, RightsEvidence.id)
        ).all()
        return tuple(rows)


def _evaluation(payload: dict[str, object]) -> RightsEvaluation:
    reasons = tuple(str(reason) for reason in payload["reasons"])
    rights_evaluation_hash = _canonical_sha256(payload)
    decision = GateDecision(
        allowed=not reasons,
        reasons=reasons,
        rights_evaluation_hash=rights_evaluation_hash,
    )
    return RightsEvaluation(
        decision=decision,
        payload={
            **payload,
            "allowed": decision.allowed,
            "rights_evaluation_hash": rights_evaluation_hash,
        },
    )


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _iso(value: object | None) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return None
