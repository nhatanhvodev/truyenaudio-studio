from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ChapterState,
    ImportKind,
    MasterResult,
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
    SourceSegment,
    SpeechSegment,
    TranslationRun,
    TranslationSegment,
    VoicePlan,
    VoicePreset,
    VoiceRole,
)
from app.modules.exports.schemas import PublicationMetadata
from app.modules.exports.workflow import BundleVerificationError, ExportWorkflow


NOW = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)
EXPECTED_PUBLICATION_FILES = {
    "tap-0001.mp3",
    "metadata.json",
    "ban-dich.md",
    "transcript.srt",
    "production-report.json",
    "provenance.json",
    "THIRD_PARTY_LICENSES.txt",
    "checksums.sha256",
}


@pytest.fixture
def workflow(db_session, artifact_store) -> ExportWorkflow:
    return ExportWorkflow(
        db_session,
        artifact_root=artifact_store.resolve(),
        audio_processor=DeterministicProbe(),
        now=lambda: NOW,
    )


@pytest.fixture
def cleared_chapter(db_session, artifact_store) -> Chapter:
    return _ready_publication_chapter(db_session, artifact_store.resolve())


def test_publication_bundle_v1_has_exact_files_and_verified_metadata(
    workflow: ExportWorkflow,
    cleared_chapter: Chapter,
    artifact_store,
) -> None:
    export = workflow.build_publication_bundle(
        cleared_chapter.id,
        PublicationMetadata("Tap mot", 1, False),
    )

    assert set(export.files) == EXPECTED_PUBLICATION_FILES
    assert export.status == "READY"

    with zipfile.ZipFile(export.zip_path) as bundle:
        assert set(bundle.namelist()) == EXPECTED_PUBLICATION_FILES
        metadata = json.loads(bundle.read("metadata.json"))
        report = json.loads(bundle.read("production-report.json"))
        provenance = json.loads(bundle.read("provenance.json"))
        checksums = _parse_checksums(bundle.read("checksums.sha256").decode("utf-8"))

    assert metadata == {
        "schema_version": "truyenaudio-studio.export.v1",
        "episode_title": "Tap mot",
        "suggested_episode_number": 1,
        "is_premium": False,
        "audio_file": "tap-0001.mp3",
        "duration_seconds": 61,
        "language": "vi-VN",
    }
    assert checksums.keys() == EXPECTED_PUBLICATION_FILES - {"checksums.sha256"}
    assert report["prompt_version"] == "translation-v1"
    report_text = json.dumps(report).lower()
    assert "full_prompt" not in report_text
    assert "secret" not in report_text
    assert "evidence file contents" not in report_text
    assert provenance["source"] == {
        "reference": "https://example.test/source",
        "type": "LICENSED_PARTNER",
    }
    assert provenance["rights"]["allowed"] is True
    assert provenance["evidence"] == [
        {"display_name": "contract.txt", "sha256": "1" * 64},
    ]
    assert artifact_store.resolve("exports/builds").is_dir()


def test_verify_bundle_fails_after_mp3_tamper(
    workflow: ExportWorkflow, cleared_chapter: Chapter
) -> None:
    export = workflow.build_publication_bundle(
        cleared_chapter.id,
        PublicationMetadata("Tap mot", 1, False),
    )
    mp3_path = export.directory_path / "tap-0001.mp3"
    mp3_path.write_bytes(b"tampered")

    with pytest.raises(BundleVerificationError, match="CHECKSUM_MISMATCH"):
        workflow.verify_bundle(export.directory_path)


def test_publication_bundle_fails_closed_before_writing_when_rights_missing(
    workflow: ExportWorkflow,
    db_session,
    artifact_store,
) -> None:
    chapter = _ready_publication_chapter(
        db_session,
        artifact_store.resolve(),
        rights_status=RightsStatus.REVIEW_REQUIRED,
    )

    with pytest.raises(PermissionError, match="RIGHTS_NOT_CLEARED"):
        workflow.build_publication_bundle(
            chapter.id, PublicationMetadata("Tap mot", 1, False)
        )


def test_publication_export_rejects_audio_from_previous_translation_run(
    workflow: ExportWorkflow,
    db_session,
    cleared_chapter: Chapter,
) -> None:
    old_run_id = cleared_chapter.approved_translation_run_id
    next_run = TranslationRun(
        id="018f0000-0000-7000-8000-000000009999",
        chapter_id=cleared_chapter.id,
        source_revision_id=cleared_chapter.active_source_revision_id,
        prompt_version="translation-v1",
        status=RunStatus.APPROVED.value,
        translation_text_sha256="f" * 64,
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    db_session.add(next_run)
    db_session.flush()
    cleared_chapter.approved_translation_run_id = next_run.id
    db_session.commit()

    with pytest.raises(ValueError, match="MASTER_TRANSLATION_STALE"):
        workflow.build_publication_bundle(
            cleared_chapter.id,
            PublicationMetadata("Tap mot", 1, False),
        )
    assert old_run_id != cleared_chapter.approved_translation_run_id


class DeterministicProbe:
    async def probe(
        self, path: Path, expected_sha256: str | None = None
    ) -> MasterResult:
        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected_sha256 is not None and sha256 != expected_sha256:
            raise ValueError("MASTER_CHECKSUM_MISMATCH")
        return MasterResult(
            duration_ms=61_000,
            sha256=sha256,
            codec="mp3",
            sample_rate=44_100,
            channels=1,
            bitrate_kbps=128,
            integrated_lufs=-16.0,
            true_peak_dbtp=-1.5,
        )


def _ready_publication_chapter(
    db_session,
    artifact_root: Path,
    *,
    rights_status: RightsStatus = RightsStatus.CLEARED,
) -> Chapter:
    suffix = _suffix(rights_status.value)
    project = Project(
        id=f"018f0000-0000-7000-8000-{suffix}001",
        title="Truyen",
        slug=f"truyen-{suffix}",
        source_type=SourceType.LICENSED_PARTNER.value,
        source_reference_url="https://example.test/source",
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
        translation_text_sha256=_sha("Ban dich mot.\nBan dich hai."),
        estimated_cost_vnd=1200,
        actual_cost_vnd=1000,
    )
    db_session.add(project)
    db_session.flush()
    db_session.add_all((chapter, revision, run))
    db_session.flush()
    chapter.active_source_revision_id = revision.id
    chapter.approved_translation_run_id = run.id

    source_ids = []
    for index, text in enumerate(("Ban dich mot.", "Ban dich hai."), start=1):
        source = SourceSegment(
            id=f"018f0000-0000-7000-8000-{suffix}{10 + index:03d}",
            source_revision_id=revision.id,
            segment_index=index,
            paragraph_start=index,
            paragraph_end=index,
            source_text=f"source {index}",
            source_sha256=_sha(f"source {index}"),
            segment_kind="SOURCE",
        )
        segment = TranslationSegment(
            id=f"018f0000-0000-7000-8000-{suffix}{20 + index:03d}",
            translation_run_id=run.id,
            source_segment_id=source.id,
            target_text=text,
            target_sha256=_sha(text),
            was_cache_hit=False,
            manually_edited=False,
        )
        db_session.add_all((source, segment))
        source_ids.append(segment.id)

    _write_artifact(
        db_session,
        artifact_root,
        "models/licenses/local-tts.txt",
        b"Local TTS license",
        f"018f0000-0000-7000-8000-{suffix}090",
        None,
        ArtifactKind.LICENSE_SNAPSHOT,
        "text/plain; charset=utf-8",
    )
    db_session.flush()
    preset = VoicePreset(
        id=f"018f0000-0000-7000-8000-{suffix}030",
        name="Narrator",
        locale="vi-VN",
        origin="BUILT_IN",
        speed="1.0",
        pitch="0",
        sample_rate=44_100,
        active=True,
        license_snapshot_artifact_id=f"018f0000-0000-7000-8000-{suffix}090",
    )
    plan = VoicePlan(
        id=f"018f0000-0000-7000-8000-{suffix}031",
        chapter_id=chapter.id,
        revision_no=1,
        mode="SINGLE_NARRATOR",
        narrator_preset_id=preset.id,
        plan_sha256="2" * 64,
    )
    role = VoiceRole(
        id=f"018f0000-0000-7000-8000-{suffix}032",
        voice_plan_id=plan.id,
        role_key="narrator",
        display_name="Narrator",
        voice_preset_id=preset.id,
        is_narrator=True,
    )
    db_session.add(preset)
    db_session.flush()
    db_session.add_all((plan, role))
    db_session.flush()
    chapter.active_voice_plan_id = plan.id
    for index, text in enumerate(("Ban dich mot.", "Ban dich hai."), start=1):
        db_session.add(
            SpeechSegment(
                id=f"018f0000-0000-7000-8000-{suffix}{40 + index:03d}",
                chapter_id=chapter.id,
                translation_run_id=run.id,
                voice_plan_id=plan.id,
                segment_index=index,
                translation_segment_id=source_ids[index - 1],
                role_id=role.id,
                narration_text=text,
                narration_sha256=_sha(text),
                pause_after_ms=500,
            )
        )

    _write_artifact(
        db_session,
        artifact_root,
        f"audio/{chapter.id}/masters/master.mp3",
        b"mp3 payload",
        f"018f0000-0000-7000-8000-{suffix}050",
        chapter.id,
        ArtifactKind.MASTER_MP3,
        "audio/mpeg",
        duration_ms=61_000,
        metadata={
            "codec": "mp3",
            "sample_rate": 44100,
            "channels": 1,
            "bitrate_kbps": 128,
            "translation_run_id": run.id,
            "voice_plan_id": plan.id,
        },
    )
    _write_artifact(
        db_session,
        artifact_root,
        f"audio/{chapter.id}/subtitles/transcript.srt",
        b"1\n00:00:00,000 --> 00:01:01,000\nBan dich mot.\n",
        f"018f0000-0000-7000-8000-{suffix}051",
        chapter.id,
        ArtifactKind.SRT,
        "application/x-subrip",
        duration_ms=61_000,
    )
    chapter.approved_master_artifact_id = f"018f0000-0000-7000-8000-{suffix}050"
    _rights(db_session, project.id, suffix)
    db_session.commit()
    return chapter


def _rights(db_session, project_id: str, suffix: str) -> None:
    evidence = RightsEvidence(
        id=f"018f0000-0000-7000-8000-{suffix}060",
        project_id=project_id,
        evidence_kind="CONTRACT",
        display_name="contract.txt",
        relative_path="evidence/contract.txt",
        sha256="1" * 64,
    )
    db_session.add(evidence)
    for index, scope in enumerate(
        (RightsScope.TRANSLATE_VI, RightsScope.CREATE_AUDIO, RightsScope.PUBLIC_STREAM),
        start=61,
    ):
        db_session.add(
            RightsGrant(
                id=f"018f0000-0000-7000-8000-{suffix}{index:03d}",
                project_id=project_id,
                scope=scope.value,
                territory="VN",
                allows_ai_processing=True,
                allows_third_party_cloud=False,
                valid_from=NOW - timedelta(days=1),
                expires_at=NOW + timedelta(days=1),
                evidence_id=evidence.id,
            )
        )


def _write_artifact(
    db_session,
    artifact_root: Path,
    relative_path: str,
    payload: bytes,
    artifact_id: str,
    chapter_id: str | None,
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


def _parse_checksums(value: str) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in value.splitlines():
        digest, filename = line.split("  ", maxsplit=1)
        checksums[filename] = digest
    return checksums


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _suffix(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:9]
