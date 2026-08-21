from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path

import pytest

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ChapterState,
    ExportKind,
    ImportKind,
    RightsScope,
    RightsStatus,
    RunStatus,
    SourceType,
)
from app.db.models import (
    Artifact,
    Chapter,
    Project,
    RightsEvidence,
    RightsGrant,
    SourceRevision,
    TranslationRun,
)
from app.modules.exports.workflow import ExportWorkflow


NOW = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)


@pytest.fixture
def workflow(db_session, artifact_store) -> ExportWorkflow:
    return ExportWorkflow(
        db_session, artifact_root=artifact_store.resolve(), now=lambda: NOW
    )


@pytest.fixture
def private_chapter(db_session, artifact_store) -> Chapter:
    return _ready_chapter(
        db_session,
        artifact_store,
        rights_status=RightsStatus.PRIVATE_ONLY,
        grant_scopes=(),
    )


@pytest.fixture
def cleared_without_stream(db_session, artifact_store) -> Chapter:
    return _ready_chapter(
        db_session,
        artifact_store,
        rights_status=RightsStatus.CLEARED,
        grant_scopes=(RightsScope.TRANSLATE_VI, RightsScope.CREATE_AUDIO),
    )


def test_private_only_never_gets_publication_metadata(
    workflow: ExportWorkflow, private_chapter: Chapter
) -> None:
    export = workflow.build_private_archive(private_chapter.id)

    assert "PRIVATE_ONLY.txt" in export.files
    assert "metadata.json" not in export.files


def test_publication_requires_all_three_scopes(
    workflow: ExportWorkflow, cleared_without_stream: Chapter
) -> None:
    decision = workflow.evaluate_gate(
        cleared_without_stream.id, ExportKind.PUBLICATION_BUNDLE
    )

    assert not decision.allowed
    assert "PUBLIC_STREAM" in decision.reasons


def test_expired_and_wrong_territory_grants_fail_closed(
    db_session, artifact_store
) -> None:
    chapter = _ready_chapter(
        db_session,
        artifact_store,
        rights_status=RightsStatus.CLEARED,
        grant_scopes=(
            RightsScope.TRANSLATE_VI,
            RightsScope.CREATE_AUDIO,
            RightsScope.PUBLIC_STREAM,
        ),
        territory="US",
    )
    workflow = ExportWorkflow(
        db_session, artifact_root=artifact_store.resolve(), now=lambda: NOW
    )

    wrong_territory = workflow.evaluate_gate(
        chapter.id, ExportKind.PUBLICATION_BUNDLE, territory="VN"
    )

    assert not wrong_territory.allowed
    assert "TRANSLATE_VI" in wrong_territory.reasons
    assert "CREATE_AUDIO" in wrong_territory.reasons
    assert "PUBLIC_STREAM" in wrong_territory.reasons


def test_premium_publication_requires_monetize_scope(
    db_session, artifact_store
) -> None:
    from app.modules.exports.schemas import PublicationMetadata

    chapter = _ready_chapter(
        db_session,
        artifact_store,
        rights_status=RightsStatus.CLEARED,
        grant_scopes=(
            RightsScope.TRANSLATE_VI,
            RightsScope.CREATE_AUDIO,
            RightsScope.PUBLIC_STREAM,
        ),
    )
    workflow = ExportWorkflow(
        db_session, artifact_root=artifact_store.resolve(), now=lambda: NOW
    )

    decision = workflow.evaluate_gate(
        chapter.id,
        ExportKind.PUBLICATION_BUNDLE,
        metadata=PublicationMetadata("Tap 1", 1, True),
    )

    assert not decision.allowed
    assert "MONETIZE" in decision.reasons


def test_download_publication_requires_download_scope(
    db_session, artifact_store
) -> None:
    chapter = _ready_chapter(
        db_session,
        artifact_store,
        rights_status=RightsStatus.CLEARED,
        grant_scopes=(
            RightsScope.TRANSLATE_VI,
            RightsScope.CREATE_AUDIO,
            RightsScope.PUBLIC_STREAM,
        ),
    )
    workflow = ExportWorkflow(
        db_session, artifact_root=artifact_store.resolve(), now=lambda: NOW
    )

    decision = workflow.evaluate_gate(
        chapter.id,
        ExportKind.PUBLICATION_BUNDLE,
        include_download=True,
    )

    assert not decision.allowed
    assert "DOWNLOAD" in decision.reasons


def _ready_chapter(
    db_session,
    artifact_store,
    *,
    rights_status: RightsStatus,
    grant_scopes: tuple[RightsScope, ...],
    territory: str = "VN",
) -> Chapter:
    suffix = _suffix(
        rights_status.value, ",".join(scope.value for scope in grant_scopes), territory
    )
    project = Project(
        id=f"018f0000-0000-7000-8000-{suffix}001",
        title=f"Truyen {suffix}",
        slug=f"truyen-{suffix}",
        source_type=SourceType.LICENSED_PARTNER.value,
        source_reference_url="local://source.txt",
        rights_status=rights_status.value,
        target_language="vi-VN",
    )
    chapter = Chapter(
        id=f"018f0000-0000-7000-8000-{suffix}002",
        project_id=project.id,
        ordinal=1,
        source_title="Mot",
        translated_title="Tap mot",
        state=ChapterState.READY_TO_EXPORT.value,
    )
    revision = SourceRevision(
        id=f"018f0000-0000-7000-8000-{suffix}003",
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text="source text",
        normalized_sha256=_sha("source text"),
        han_char_count=0,
        total_char_count=11,
        normalizer_version="nfc-v1",
    )
    run = TranslationRun(
        id=f"018f0000-0000-7000-8000-{suffix}004",
        chapter_id=chapter.id,
        source_revision_id=revision.id,
        prompt_version="translation-v1",
        status=RunStatus.APPROVED.value,
        translation_text_sha256=_sha("ban dich"),
    )
    db_session.add(project)
    db_session.flush()
    db_session.add_all((chapter, revision, run))
    db_session.flush()
    chapter.active_source_revision_id = revision.id
    chapter.approved_translation_run_id = run.id
    _write_artifact(
        db_session,
        artifact_store.resolve(),
        f"audio/{chapter.id}/masters/master.mp3",
        b"mp3",
        f"018f0000-0000-7000-8000-{suffix}005",
        chapter.id,
        ArtifactKind.MASTER_MP3,
        "audio/mpeg",
        duration_ms=61_000,
        metadata={"translation_run_id": run.id, "voice_plan_id": None},
    )
    chapter.approved_master_artifact_id = f"018f0000-0000-7000-8000-{suffix}005"
    for index, scope in enumerate(grant_scopes, start=6):
        evidence = RightsEvidence(
            id=f"018f0000-0000-7000-8000-{suffix}{index:03d}",
            project_id=project.id,
            evidence_kind="CONTRACT",
            display_name=f"{scope.value}.txt",
            relative_path=f"evidence/{scope.value}.txt",
            sha256=_sha(scope.value),
        )
        grant = RightsGrant(
            id=f"018f0000-0000-7000-8000-{suffix}{index + 100:03d}",
            project_id=project.id,
            scope=scope.value,
            territory=territory,
            allows_ai_processing=True,
            allows_third_party_cloud=False,
            valid_from=NOW - timedelta(days=1),
            expires_at=NOW + timedelta(days=1),
            evidence_id=evidence.id,
        )
        db_session.add_all((evidence, grant))
    db_session.commit()
    return chapter


def _write_artifact(
    db_session,
    artifact_root: Path,
    relative_path: str,
    payload: bytes,
    artifact_id: str,
    chapter_id: str,
    kind: ArtifactKind,
    mime_type: str,
    *,
    duration_ms: int | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    path = artifact_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    db_session.add(
        Artifact(
            id=artifact_id,
            chapter_id=chapter_id,
            kind=kind.value,
            status=ArtifactStatus.READY.value,
            relative_path=relative_path,
            sha256=hashlib.sha256(payload).hexdigest(),
            byte_size=len(payload),
            mime_type=mime_type,
            duration_ms=duration_ms,
            input_hash=_sha(relative_path),
            settings_hash=_sha(kind.value),
            metadata_json=metadata,
        )
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _suffix(*parts: str) -> str:
    return hashlib.sha1(":".join(parts).encode("utf-8")).hexdigest()[:9]
