from __future__ import annotations

from dataclasses import dataclass
import hashlib

import pytest

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ChapterState,
    ExportKind,
    ExportStatus,
    ImportKind,
    RightsStatus,
    RunStatus,
    SourceType,
    VoiceMode,
    VoiceOrigin,
)
from app.db.models import (
    Artifact,
    Chapter,
    Export,
    Project,
    SourceRevision,
    SourceSegment,
    SpeechSegment,
    TranslationRun,
    TranslationSegment,
    VoicePlan,
    VoicePreset,
    VoiceRole,
)
from app.modules.projects.invalidation import Change, InvalidationGraph


@dataclass(frozen=True)
class InvalidationFixture:
    project: str
    chapter: str
    source_revision: str
    translation_run: str
    translation_segment_1: str
    translation_segment_2: str
    voice_plan: str
    voice_role_1: str
    voice_role_2: str
    speech_1: str
    speech_2: str
    audio_1: str
    audio_2: str
    master: str
    srt: str
    export_artifact: str
    export: str


def test_target_edit_invalidates_only_dependent_speech_audio(db_session) -> None:
    fixture = _ready_chapter_fixture(db_session)
    graph = InvalidationGraph(db_session)

    plan = graph.plan(Change.target_text(fixture.translation_segment_2))

    assert fixture.audio_1 in plan.reused
    assert {fixture.audio_2, fixture.master, fixture.srt, fixture.export} <= set(plan.invalidated)
    assert plan.revoked_exports == (fixture.export,)
    assert _artifact(db_session, fixture.audio_1).status == ArtifactStatus.READY.value
    assert _artifact(db_session, fixture.audio_2).status == ArtifactStatus.SUPERSEDED.value
    assert _artifact(db_session, fixture.master).status == ArtifactStatus.SUPERSEDED.value
    assert _export(db_session, fixture.export).status == ExportStatus.REVOKED.value

    chapter = db_session.get(Chapter, fixture.chapter)
    assert chapter is not None
    assert chapter.approved_translation_run_id == fixture.translation_run
    assert chapter.active_voice_plan_id == fixture.voice_plan
    assert chapter.approved_master_artifact_id is None
    assert chapter.last_export_id is None


@pytest.mark.parametrize(
    ("change_name", "expected_invalidated", "expected_reused", "clears_translation", "clears_voice", "clears_master"),
    (
        (
            "project_metadata",
            {"translation_run", "audio_1", "audio_2", "master", "srt", "export_artifact", "export"},
            set(),
            True,
            True,
            True,
        ),
        (
            "source_revision",
            {"translation_run", "audio_1", "audio_2", "master", "srt", "export_artifact", "export"},
            set(),
            True,
            True,
            True,
        ),
        (
            "glossary",
            {"translation_run", "audio_1", "audio_2", "master", "srt", "export_artifact", "export"},
            set(),
            True,
            True,
            True,
        ),
        (
            "story_memory",
            {"translation_run", "audio_1", "audio_2", "master", "srt", "export_artifact", "export"},
            set(),
            True,
            True,
            True,
        ),
        ("pronunciation", {"audio_1", "master", "srt", "export_artifact", "export"}, {"audio_2"}, False, False, True),
        ("voice_role", {"audio_1", "master", "srt", "export_artifact", "export"}, {"audio_2"}, False, False, True),
        ("master_settings", {"master", "srt", "export_artifact", "export"}, {"audio_1", "audio_2"}, False, False, True),
        ("rights_evidence", {"export_artifact", "export"}, {"audio_1", "audio_2", "master", "srt"}, False, False, False),
    ),
)
def test_invalidation_matrix_updates_statuses_and_active_pointers(
    db_session,
    change_name: str,
    expected_invalidated: set[str],
    expected_reused: set[str],
    clears_translation: bool,
    clears_voice: bool,
    clears_master: bool,
) -> None:
    fixture = _ready_chapter_fixture(db_session)
    graph = InvalidationGraph(db_session)

    plan = graph.plan(_change(change_name, fixture))

    expected_invalidated_ids = {getattr(fixture, name) for name in expected_invalidated}
    expected_reused_ids = {getattr(fixture, name) for name in expected_reused}
    assert expected_invalidated_ids <= set(plan.invalidated)
    assert expected_reused_ids <= set(plan.reused)

    for name in expected_invalidated & {"audio_1", "audio_2", "master", "srt", "export_artifact"}:
        assert _artifact(db_session, getattr(fixture, name)).status == ArtifactStatus.SUPERSEDED.value
    for name in expected_reused & {"audio_1", "audio_2", "master", "srt"}:
        assert _artifact(db_session, getattr(fixture, name)).status == ArtifactStatus.READY.value
    if "translation_run" in expected_invalidated:
        assert _run(db_session, fixture.translation_run).status == RunStatus.SUPERSEDED.value
    assert _export(db_session, fixture.export).status == ExportStatus.REVOKED.value

    chapter = db_session.get(Chapter, fixture.chapter)
    assert chapter is not None
    expected_translation_run = None if clears_translation else fixture.translation_run
    expected_voice_plan = None if clears_voice else fixture.voice_plan
    expected_master = None if clears_master else fixture.master
    assert chapter.approved_translation_run_id == expected_translation_run
    assert chapter.active_voice_plan_id == expected_voice_plan
    assert chapter.approved_master_artifact_id == expected_master
    assert chapter.last_export_id is None


def test_invalidation_never_deletes_artifact_files(db_session, artifact_store) -> None:
    fixture = _ready_chapter_fixture(db_session, artifact_root=artifact_store.resolve())
    graph = InvalidationGraph(db_session)
    paths = [
        artifact_store.resolve(_artifact(db_session, artifact_id).relative_path)
        for artifact_id in (fixture.audio_1, fixture.audio_2, fixture.master, fixture.srt, fixture.export_artifact)
    ]

    graph.plan(Change.source_revision(fixture.source_revision))

    assert all(path.is_file() for path in paths)


def _change(name: str, fixture: InvalidationFixture) -> Change:
    if name == "project_metadata":
        return Change.project_metadata(fixture.project)
    if name == "source_revision":
        return Change.source_revision(fixture.source_revision)
    if name == "glossary":
        return Change.glossary(fixture.project)
    if name == "story_memory":
        return Change.story_memory(fixture.project)
    if name == "pronunciation":
        return Change.pronunciation(fixture.speech_1)
    if name == "voice_role":
        return Change.voice_role(fixture.voice_role_1)
    if name == "master_settings":
        return Change.master_settings(fixture.chapter)
    if name == "rights_evidence":
        return Change.rights_evidence(fixture.project)
    raise AssertionError(name)


def _ready_chapter_fixture(db_session, artifact_root=None) -> InvalidationFixture:
    project_id = _id(1)
    chapter_id = _id(2)
    revision_id = _id(3)
    source_1_id = _id(4)
    source_2_id = _id(5)
    run_id = _id(6)
    target_1_id = _id(7)
    target_2_id = _id(8)
    preset_id = _id(9)
    plan_id = _id(10)
    role_1_id = _id(11)
    role_2_id = _id(12)
    speech_1_id = _id(13)
    speech_2_id = _id(14)
    audio_1_id = _id(15)
    audio_2_id = _id(16)
    master_id = _id(17)
    srt_id = _id(18)
    export_artifact_id = _id(19)
    export_id = _id(20)

    project = Project(
        id=project_id,
        title="Novel",
        slug=f"novel-{chapter_id[-2:]}",
        source_type=SourceType.SELF_AUTHORED.value,
        rights_status=RightsStatus.CLEARED.value,
    )
    chapter = Chapter(
        id=chapter_id,
        project_id=project_id,
        ordinal=1,
        source_title="Chapter 1",
        state=ChapterState.READY_TO_EXPORT.value,
    )
    db_session.add(project)
    db_session.flush()
    db_session.add(chapter)
    db_session.flush()
    revision = SourceRevision(
        id=revision_id,
        chapter_id=chapter_id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text="Mot\nHai",
        normalized_sha256=_sha("source"),
        han_char_count=0,
        total_char_count=7,
        normalizer_version="nfc-v1",
    )
    source_1 = SourceSegment(
        id=source_1_id,
        source_revision_id=revision_id,
        segment_index=1,
        paragraph_start=0,
        paragraph_end=0,
        source_text="Mot",
        source_sha256=_sha("Mot"),
        segment_kind="SOURCE",
    )
    source_2 = SourceSegment(
        id=source_2_id,
        source_revision_id=revision_id,
        segment_index=2,
        paragraph_start=1,
        paragraph_end=1,
        source_text="Hai",
        source_sha256=_sha("Hai"),
        segment_kind="SOURCE",
    )
    run = TranslationRun(
        id=run_id,
        chapter_id=chapter_id,
        source_revision_id=revision_id,
        prompt_version="translation-v1",
        glossary_revision_hash=_sha("glossary"),
        story_memory_revision_hash=_sha("memory"),
        status=RunStatus.APPROVED.value,
        translation_text_sha256=_sha("run"),
    )
    target_1 = TranslationSegment(
        id=target_1_id,
        translation_run_id=run_id,
        source_segment_id=source_1_id,
        target_text="Mot dich",
        target_sha256=_sha("Mot dich"),
    )
    target_2 = TranslationSegment(
        id=target_2_id,
        translation_run_id=run_id,
        source_segment_id=source_2_id,
        target_text="Hai dich",
        target_sha256=_sha("Hai dich"),
    )
    preset = VoicePreset(
        id=preset_id,
        name="Narrator",
        locale="vi-VN",
        origin=VoiceOrigin.BUILT_IN.value,
        speed="1.0",
        pitch="0",
        sample_rate=22050,
        active=True,
    )
    plan = VoicePlan(
        id=plan_id,
        chapter_id=chapter_id,
        revision_no=1,
        mode=VoiceMode.SINGLE_NARRATOR.value,
        narrator_preset_id=preset_id,
        plan_sha256=_sha("plan"),
    )
    role_1 = VoiceRole(
        id=role_1_id,
        voice_plan_id=plan_id,
        role_key="narrator-a",
        display_name="Narrator A",
        voice_preset_id=preset_id,
        is_narrator=True,
    )
    role_2 = VoiceRole(
        id=role_2_id,
        voice_plan_id=plan_id,
        role_key="narrator-b",
        display_name="Narrator B",
        voice_preset_id=preset_id,
        is_narrator=False,
    )
    speech_1 = SpeechSegment(
        id=speech_1_id,
        chapter_id=chapter_id,
        translation_run_id=run_id,
        voice_plan_id=plan_id,
        segment_index=1,
        translation_segment_id=target_1_id,
        role_id=role_1_id,
        narration_text="Mot dich",
        narration_sha256=_sha("Mot dich"),
        pause_before_ms=0,
        pause_after_ms=500,
        pronunciation_revision_hash=_sha("pronunciation-1"),
    )
    speech_2 = SpeechSegment(
        id=speech_2_id,
        chapter_id=chapter_id,
        translation_run_id=run_id,
        voice_plan_id=plan_id,
        segment_index=2,
        translation_segment_id=target_2_id,
        role_id=role_2_id,
        narration_text="Hai dich",
        narration_sha256=_sha("Hai dich"),
        pause_before_ms=0,
        pause_after_ms=500,
        pronunciation_revision_hash=_sha("pronunciation-2"),
    )
    artifacts = [
        _ready_artifact(audio_1_id, chapter_id, ArtifactKind.TTS_SEGMENT, "audio-1.wav", {"speech_segment_id": speech_1_id}),
        _ready_artifact(audio_2_id, chapter_id, ArtifactKind.TTS_SEGMENT, "audio-2.wav", {"speech_segment_id": speech_2_id}),
        _ready_artifact(master_id, chapter_id, ArtifactKind.MASTER_MP3, "master.mp3", {"translation_run_id": run_id, "voice_plan_id": plan_id}),
        _ready_artifact(srt_id, chapter_id, ArtifactKind.SRT, "subtitles.srt", {"master_sha256": _sha(master_id)}),
        _ready_artifact(export_artifact_id, chapter_id, ArtifactKind.PUBLICATION_BUNDLE, "export.zip", {"export_id": export_id}),
    ]
    export = Export(
        id=export_id,
        chapter_id=chapter_id,
        kind=ExportKind.PUBLICATION_BUNDLE.value,
        status=ExportStatus.READY.value,
        bundle_artifact_id=export_artifact_id,
        manifest_sha256=_sha("manifest"),
    )
    for rows in (
        (revision,),
        (source_1, source_2),
        (run,),
        (target_1, target_2),
        (preset,),
        (plan,),
        (role_1, role_2),
        (speech_1, speech_2),
        tuple(artifacts),
        (export,),
    ):
        db_session.add_all(rows)
        db_session.flush()
    chapter.active_source_revision_id = revision_id
    chapter.approved_translation_run_id = run_id
    chapter.active_voice_plan_id = plan_id
    chapter.approved_master_artifact_id = master_id
    chapter.last_export_id = export_id
    db_session.commit()
    if artifact_root is not None:
        for artifact in artifacts:
            path = artifact_root / artifact.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(artifact.id.encode("ascii"))
    return InvalidationFixture(
        project=project_id,
        chapter=chapter_id,
        source_revision=revision_id,
        translation_run=run_id,
        translation_segment_1=target_1_id,
        translation_segment_2=target_2_id,
        voice_plan=plan_id,
        voice_role_1=role_1_id,
        voice_role_2=role_2_id,
        speech_1=speech_1_id,
        speech_2=speech_2_id,
        audio_1=audio_1_id,
        audio_2=audio_2_id,
        master=master_id,
        srt=srt_id,
        export_artifact=export_artifact_id,
        export=export_id,
    )


def _ready_artifact(
    artifact_id: str,
    chapter_id: str,
    kind: ArtifactKind,
    filename: str,
    metadata: dict[str, str],
) -> Artifact:
    return Artifact(
        id=artifact_id,
        chapter_id=chapter_id,
        kind=kind.value,
        status=ArtifactStatus.READY.value,
        relative_path=f"fixtures/{artifact_id}-{filename}",
        sha256=_sha(artifact_id),
        byte_size=len(artifact_id),
        mime_type="application/octet-stream",
        duration_ms=1000,
        input_hash=_sha(f"input:{artifact_id}"),
        settings_hash=_sha(f"settings:{artifact_id}"),
        metadata_json=metadata,
    )


def _artifact(db_session, artifact_id: str) -> Artifact:
    artifact = db_session.get(Artifact, artifact_id)
    assert artifact is not None
    return artifact


def _export(db_session, export_id: str) -> Export:
    export = db_session.get(Export, export_id)
    assert export is not None
    return export


def _run(db_session, run_id: str) -> TranslationRun:
    run = db_session.get(TranslationRun, run_id)
    assert run is not None
    return run


def _id(value: int) -> str:
    return f"018f0000-0000-7000-8000-{value:012d}"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
