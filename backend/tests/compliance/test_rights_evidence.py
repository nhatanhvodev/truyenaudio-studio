from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.contracts import EvidenceKind, RightsScope, RightsStatus, SourceType
from app.db.models import Artifact, RightsEvidence
from app.modules.compliance.evidence import CreateGrant, EvidenceService, EvidenceUpload, GrantService
from app.modules.projects.workflow import CreateProject, ProjectWorkflow


@pytest.fixture
def workflow(db_session, artifact_store) -> ProjectWorkflow:
    return ProjectWorkflow(db_session, artifact_store)


@pytest.fixture
def project(workflow: ProjectWorkflow):
    return workflow.create_project(
        CreateProject(
            "Rights",
            "rights",
            SourceType.USER_SUPPLIED_PRIVATE,
            RightsStatus.PRIVATE_ONLY,
            None,
            None,
        )
    )


@pytest.fixture
def evidence_service(db_session, artifact_store) -> EvidenceService:
    return EvidenceService(db_session, artifact_store)


@pytest.fixture
def grant_service(db_session) -> GrantService:
    return GrantService(db_session)


def test_rights_evidence_is_local_and_grant_references_it(
    evidence_service: EvidenceService,
    grant_service: GrantService,
    project,
    db_session,
) -> None:
    evidence = evidence_service.store(
        project.id,
        EvidenceUpload("permission.txt", b"owner permits audio", EvidenceKind.AUTHOR_PERMISSION, "Author", None),
    )
    grant = grant_service.create(
        project.id,
        CreateGrant(RightsScope.PUBLIC_STREAM, "VN", True, False, date(2026, 8, 19), None, evidence.id),
    )

    assert evidence.relative_path.startswith(f"projects/{project.slug}/evidence/")
    assert grant.evidence_id == evidence.id
    artifact = db_session.get(Artifact, evidence.artifact_id)
    assert artifact is not None
    assert artifact.kind == "RIGHTS_EVIDENCE"


def test_evidence_rejects_oversized_and_mismatched_magic_bytes(evidence_service: EvidenceService, project) -> None:
    with pytest.raises(ValueError, match="RIGHTS_EVIDENCE_TOO_LARGE"):
        evidence_service.store(
            project.id,
            EvidenceUpload("large.txt", b"x" * (10 * 1024 * 1024 + 1), EvidenceKind.AUTHOR_PERMISSION, None, None),
        )

    with pytest.raises(ValueError, match="RIGHTS_EVIDENCE_TYPE_MISMATCH"):
        evidence_service.store(
            project.id,
            EvidenceUpload("fake.pdf", b"not a pdf", EvidenceKind.AUTHOR_PERMISSION, None, None),
        )


def test_evidence_filename_is_sanitized_to_basename(
    evidence_service: EvidenceService,
    project,
    db_session,
) -> None:
    evidence = evidence_service.store(
        project.id,
        EvidenceUpload("..\\private\\permission.txt", b"permission", EvidenceKind.USER_ATTESTATION, None, None),
    )

    assert evidence.display_name == "permission.txt"
    assert ".." not in evidence.relative_path
    stored = db_session.get(RightsEvidence, evidence.id)
    assert stored is not None
    assert stored.display_name == "permission.txt"


def test_grant_validates_dates_territory_and_evidence_project(
    workflow: ProjectWorkflow,
    evidence_service: EvidenceService,
    grant_service: GrantService,
    project,
) -> None:
    other = workflow.create_project(
        CreateProject("Other", "other", SourceType.USER_SUPPLIED_PRIVATE, RightsStatus.PRIVATE_ONLY, None, None)
    )
    other_evidence = evidence_service.store(
        other.id,
        EvidenceUpload("other.txt", b"permission", EvidenceKind.USER_ATTESTATION, None, None),
    )

    with pytest.raises(ValueError, match="RIGHTS_TERRITORY_REQUIRED"):
        grant_service.create(
            project.id,
            CreateGrant(RightsScope.PUBLIC_STREAM, "", True, False, date(2026, 8, 19), None, None),
        )

    with pytest.raises(ValueError, match="RIGHTS_DATE_RANGE_INVALID"):
        grant_service.create(
            project.id,
            CreateGrant(
                RightsScope.PUBLIC_STREAM,
                "VN",
                True,
                False,
                date(2026, 8, 20),
                datetime(2026, 8, 19, tzinfo=UTC),
                None,
            ),
        )

    with pytest.raises(ValueError, match="RIGHTS_EVIDENCE_PROJECT_MISMATCH"):
        grant_service.create(
            project.id,
            CreateGrant(RightsScope.PUBLIC_STREAM, "VN", True, False, date(2026, 8, 19), None, other_evidence.id),
        )


def test_grant_requires_explicit_boolean_permissions(grant_service: GrantService, project) -> None:
    with pytest.raises(ValueError, match="RIGHTS_BOOLEAN_REQUIRED"):
        grant_service.create(
            project.id,
            CreateGrant(RightsScope.PUBLIC_STREAM, "VN", 1, False, date(2026, 8, 19), None, None),
        )
