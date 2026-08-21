from __future__ import annotations

import hashlib
import math
from pathlib import Path
import wave

import pytest

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ChapterState,
    ImportKind,
    QaCategory,
    QaSeverity,
    QaStatus,
    RightsStatus,
    RunStatus,
    SourceType,
    SynthesisResult,
    Usage,
    UsageUnit,
    VoiceMode,
    VoiceOrigin,
)
from app.db.models import (
    Artifact,
    Chapter,
    Project,
    QaIssue,
    SourceRevision,
    SourceSegment,
    SpeechSegment,
    TranslationRun,
    TranslationSegment,
    VoicePreset,
)
from app.modules.speech.workflow import (
    AudioApprovalBlocked,
    AudioApprovalConflict,
    SpeechWorkflow,
    TranslationApprovalRequired,
    VoicePlanRequired,
)


def test_single_plan_has_exactly_one_narrator(
    db_session, deterministic_uuid7_factory
) -> None:
    fixture = _approved_chapter(db_session)
    preset = _voice_preset(db_session)
    workflow = SpeechWorkflow(db_session, id_factory=deterministic_uuid7_factory)

    plan = workflow.configure_single(fixture.chapter_id, preset.id)

    assert plan.mode is VoiceMode.SINGLE_NARRATOR
    assert len(plan.roles) == 1
    assert plan.roles[0].is_narrator is True
    assert plan.roles[0].voice_preset_id == preset.id
    assert len(plan.segments) == 3
    assert all(
        20_000 <= segment.estimated_duration_ms <= 60_000 for segment in plan.segments
    )
    assert "bon muoi hai ki lo met" in plan.segments[0].narration_text
    assert db_session.get(
        TranslationSegment, fixture.translation_segment_ids[0]
    ).target_text == (
        "Doan mot co 42 km. " + "Cau van ngan giu nhip ke chuyen am ap va ro rang. " * 8
    )
    assert (
        db_session.get(Chapter, fixture.chapter_id).state
        == ChapterState.VOICE_CONFIGURED.value
    )


def test_single_plan_requires_approved_translation(
    db_session, deterministic_uuid7_factory
) -> None:
    fixture = _review_chapter(db_session)
    preset = _voice_preset(db_session)
    workflow = SpeechWorkflow(db_session, id_factory=deterministic_uuid7_factory)

    with pytest.raises(TranslationApprovalRequired):
        workflow.configure_single(fixture.chapter_id, preset.id)


def test_default_render_refuses_fake_tts_without_explicit_injected_adapter(
    db_session, tmp_path: Path, deterministic_uuid7_factory
) -> None:
    fixture = _approved_chapter(db_session)
    preset = _voice_preset(db_session)
    workflow = SpeechWorkflow(
        db_session,
        audio_processor=DeterministicAudioProcessor(),
        artifact_root=tmp_path,
        id_factory=deterministic_uuid7_factory,
    )
    workflow.configure_single(fixture.chapter_id, preset.id)

    with pytest.raises(ValueError, match="LOCAL_TTS_ADAPTER_REQUIRED"):
        workflow.enqueue_render(fixture.chapter_id)


def test_render_rejects_unverified_fake_voice_preset_even_with_fake_e2e_adapter(
    db_session, tmp_path: Path, deterministic_uuid7_factory
) -> None:
    fixture = _approved_chapter(db_session)
    preset = _voice_preset(db_session, provider_voice_id="fake-vi-narrator")
    preset.model_snapshot_hash = None
    preset.license_snapshot_artifact_id = None
    db_session.commit()
    workflow = SpeechWorkflow(
        db_session,
        tts=CountingTts(),
        audio_processor=DeterministicAudioProcessor(),
        artifact_root=tmp_path,
        id_factory=deterministic_uuid7_factory,
        allow_fake_tts=False,
    )
    workflow.configure_single(fixture.chapter_id, preset.id)

    with pytest.raises(ValueError, match="VOICE_MODEL_LICENSE_UNVERIFIED"):
        workflow.enqueue_render(fixture.chapter_id)


def test_fake_e2e_adapter_still_renders_when_explicitly_allowed(
    db_session, tmp_path: Path, deterministic_uuid7_factory
) -> None:
    fixture = _approved_chapter(db_session)
    preset = _voice_preset(db_session, provider_voice_id="fake-vi-narrator")
    workflow = SpeechWorkflow(
        db_session,
        tts=CountingTts(),
        audio_processor=DeterministicAudioProcessor(),
        artifact_root=tmp_path,
        id_factory=deterministic_uuid7_factory,
        allow_fake_tts=True,
    )
    workflow.configure_single(fixture.chapter_id, preset.id)

    rendered = workflow.enqueue_render(fixture.chapter_id)

    assert rendered.rendered_segment_ids


def test_target_edit_only_invalidates_corresponding_audio(
    db_session, tmp_path: Path, deterministic_uuid7_factory
) -> None:
    fixture = _approved_chapter(db_session)
    preset = _voice_preset(db_session)
    tts = CountingTts()
    workflow = SpeechWorkflow(
        db_session,
        tts=tts,
        audio_processor=DeterministicAudioProcessor(),
        artifact_root=tmp_path,
        id_factory=deterministic_uuid7_factory,
    )
    plan = workflow.configure_single(fixture.chapter_id, preset.id)
    rendered = workflow.enqueue_render(fixture.chapter_id)

    changed = workflow.regenerate_segments(
        fixture.chapter_id, (rendered.segment_ids[1],)
    )

    assert changed.reused_segment_ids == (
        rendered.segment_ids[0],
        rendered.segment_ids[2],
    )
    assert changed.rendered_segment_ids == (rendered.segment_ids[1],)
    assert tts.requested_segment_ids == list(rendered.segment_ids) + [
        rendered.segment_ids[1]
    ]
    assert changed.master_artifact_id != rendered.master_artifact_id
    assert tuple(segment.id for segment in plan.segments) == rendered.segment_ids


def test_render_requires_voice_plan_for_current_approved_translation(
    db_session, tmp_path: Path, deterministic_uuid7_factory
) -> None:
    fixture = _approved_chapter(db_session)
    preset = _voice_preset(db_session)
    workflow = SpeechWorkflow(
        db_session,
        tts=CountingTts(),
        audio_processor=DeterministicAudioProcessor(),
        artifact_root=tmp_path,
        id_factory=deterministic_uuid7_factory,
    )
    workflow.configure_single(fixture.chapter_id, preset.id)
    next_run = TranslationRun(
        id="018f0000-0000-7000-8000-400000000099",
        chapter_id=fixture.chapter_id,
        source_revision_id="018f0000-0000-7000-8000-300000000001",
        prompt_version="translation-v1",
        status=RunStatus.APPROVED.value,
        translation_text_sha256="c" * 64,
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    db_session.add(next_run)
    db_session.flush()
    db_session.get(
        Chapter, fixture.chapter_id
    ).approved_translation_run_id = next_run.id
    db_session.commit()

    with pytest.raises(VoicePlanRequired, match="VOICE_PLAN_STALE_TRANSLATION"):
        workflow.enqueue_render(fixture.chapter_id)


def test_audio_approval_is_independent_and_blocks_open_major_audio_issues(
    db_session, tmp_path: Path, deterministic_uuid7_factory
) -> None:
    fixture = _approved_chapter(db_session)
    preset = _voice_preset(db_session)
    workflow = SpeechWorkflow(
        db_session,
        tts=CountingTts(),
        audio_processor=DeterministicAudioProcessor(),
        artifact_root=tmp_path,
        id_factory=deterministic_uuid7_factory,
    )
    workflow.configure_single(fixture.chapter_id, preset.id)
    rendered = workflow.enqueue_render(fixture.chapter_id)
    db_session.add(
        QaIssue(
            id=deterministic_uuid7_factory(),
            chapter_id=fixture.chapter_id,
            speech_segment_id=rendered.segment_ids[0],
            category=QaCategory.CLIPPING.value,
            severity=QaSeverity.MAJOR.value,
            status=QaStatus.OPEN.value,
            evidence="segment peak -0.2 dBFS",
            suggestion="Regenerate or lower gain.",
            rule_or_model="audio-qa-v1",
        )
    )
    db_session.commit()

    with pytest.raises(AudioApprovalBlocked):
        workflow.approve_audio(
            fixture.chapter_id,
            rendered.master_artifact_id,
            expected_sha256=rendered.master_sha256,
        )

    issue = (
        db_session.query(QaIssue)
        .filter(QaIssue.category == QaCategory.CLIPPING.value)
        .one()
    )
    issue.status = QaStatus.FIXED.value
    db_session.commit()
    approved = workflow.approve_audio(
        fixture.chapter_id,
        rendered.master_artifact_id,
        expected_sha256=rendered.master_sha256,
    )

    chapter = db_session.get(Chapter, fixture.chapter_id)
    assert approved.status == "APPROVED"
    assert chapter.approved_translation_run_id == fixture.translation_run_id
    assert chapter.approved_master_artifact_id == rendered.master_artifact_id
    assert chapter.state == ChapterState.READY_TO_EXPORT.value


def test_audio_approval_rejects_master_from_previous_translation_run(
    db_session, tmp_path: Path, deterministic_uuid7_factory
) -> None:
    fixture = _approved_chapter(db_session)
    preset = _voice_preset(db_session)
    workflow = SpeechWorkflow(
        db_session,
        tts=CountingTts(),
        audio_processor=DeterministicAudioProcessor(),
        artifact_root=tmp_path,
        id_factory=deterministic_uuid7_factory,
    )
    workflow.configure_single(fixture.chapter_id, preset.id)
    rendered = workflow.enqueue_render(fixture.chapter_id)
    next_run = TranslationRun(
        id="018f0000-0000-7000-8000-400000000098",
        chapter_id=fixture.chapter_id,
        source_revision_id="018f0000-0000-7000-8000-300000000001",
        prompt_version="translation-v1",
        status=RunStatus.APPROVED.value,
        translation_text_sha256="d" * 64,
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    db_session.add(next_run)
    db_session.flush()
    chapter = db_session.get(Chapter, fixture.chapter_id)
    chapter.approved_translation_run_id = next_run.id
    db_session.commit()

    with pytest.raises(AudioApprovalConflict, match="MASTER_TRANSLATION_STALE"):
        workflow.approve_audio(
            fixture.chapter_id,
            rendered.master_artifact_id,
            expected_sha256=rendered.master_sha256,
        )


@pytest.mark.parametrize(
    ("tts", "category"),
    [
        pytest.param("clipped", QaCategory.CLIPPING, id="clipped-premaster-segment"),
        pytest.param("silent", QaCategory.SILENCE, id="long-silence-premaster-segment"),
    ],
)
def test_premaster_audio_qa_generates_blocking_issue_before_approval(
    db_session,
    tmp_path: Path,
    deterministic_uuid7_factory,
    tts: str,
    category: QaCategory,
) -> None:
    fixture = _approved_chapter(db_session)
    preset = _voice_preset(db_session)
    tts_adapter = WavTts(mode=tts)
    workflow = SpeechWorkflow(
        db_session,
        tts=tts_adapter,
        audio_processor=DeterministicAudioProcessor(),
        artifact_root=tmp_path,
        id_factory=deterministic_uuid7_factory,
    )
    workflow.configure_single(fixture.chapter_id, preset.id)
    rendered = workflow.enqueue_render(fixture.chapter_id)

    issues = db_session.query(QaIssue).filter(QaIssue.category == category.value).all()
    assert issues
    assert all(issue.status == QaStatus.OPEN.value for issue in issues)
    assert all(issue.severity == QaSeverity.MAJOR.value for issue in issues)
    with pytest.raises(AudioApprovalBlocked):
        workflow.approve_audio(
            fixture.chapter_id,
            rendered.master_artifact_id,
            expected_sha256=rendered.master_sha256,
        )


def test_long_pause_generates_blocking_silence_issue_before_approval(
    db_session,
    tmp_path: Path,
    deterministic_uuid7_factory,
) -> None:
    fixture = _approved_chapter(db_session)
    preset = _voice_preset(db_session)
    workflow = SpeechWorkflow(
        db_session,
        tts=WavTts(mode="normal"),
        audio_processor=DeterministicAudioProcessor(),
        artifact_root=tmp_path,
        id_factory=deterministic_uuid7_factory,
    )
    plan = workflow.configure_single(fixture.chapter_id, preset.id)
    paused_segment = db_session.get(SpeechSegment, plan.segments[0].id)
    paused_segment.pause_after_ms = 8_500
    db_session.commit()

    rendered = workflow.enqueue_render(fixture.chapter_id)

    issues = (
        db_session.query(QaIssue)
        .filter(QaIssue.category == QaCategory.SILENCE.value)
        .all()
    )
    assert issues
    assert all(issue.status == QaStatus.OPEN.value for issue in issues)
    assert all(issue.severity == QaSeverity.MAJOR.value for issue in issues)
    assert any("pause_after_ms=8500" in (issue.evidence or "") for issue in issues)
    with pytest.raises(AudioApprovalBlocked):
        workflow.approve_audio(
            fixture.chapter_id,
            rendered.master_artifact_id,
            expected_sha256=rendered.master_sha256,
        )


class CountingTts:
    def __init__(self) -> None:
        self.requested_segment_ids: list[str] = []

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": "fake",
            "model": "fake-tts",
            "provider_version": "1",
            "sample_rates": [44_100],
            "formats": ["wav"],
            "network": False,
        }

    async def list_voices(self, locale: str) -> list[dict[str, object]]:
        return [{"id": "voice-1", "locale": locale, "sample_rate": 44_100}]

    async def synthesize(self, request, output_path: Path) -> SynthesisResult:
        self.requested_segment_ids.append(request.speech_segment_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = f"wav:{request.speech_segment_id}:{request.narration_text}".encode()
        output_path.write_bytes(payload)
        return SynthesisResult(
            provider="fake",
            model="fake-tts",
            provider_version="1",
            duration_ms=30_000,
            sha256=hashlib.sha256(payload).hexdigest(),
            usage=(Usage(UsageUnit.AUDIO_SECOND.value, 30),),
        )


class WavTts:
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": "fake",
            "model": f"fake-{self.mode}-tts",
            "provider_version": "1",
            "sample_rates": [44_100],
            "formats": ["wav"],
            "network": False,
        }

    async def list_voices(self, locale: str) -> list[dict[str, object]]:
        return [{"id": "voice-1", "locale": locale, "sample_rate": 44_100}]

    async def synthesize(self, request, output_path: Path) -> SynthesisResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration_seconds = 9.2 if self.mode == "silent" else 1.0
        frame_count = round(44_100 * duration_seconds)
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(44_100)
            if self.mode == "silent":
                wav.writeframes(b"\x00\x00" * frame_count)
            elif self.mode == "normal":
                frames = bytearray()
                for index in range(frame_count):
                    sample = round(8_000 * math.sin(2 * math.pi * 440 * index / 44_100))
                    frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
                wav.writeframes(bytes(frames))
            else:
                frames = bytearray()
                for index in range(frame_count):
                    sample = 32_767 if index % 2 == 0 else -32_768
                    frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
                wav.writeframes(bytes(frames))
        return SynthesisResult(
            provider="fake",
            model=f"fake-{self.mode}-tts",
            provider_version="1",
            duration_ms=round(duration_seconds * 1000),
            sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
            usage=(Usage(UsageUnit.AUDIO_SECOND.value, math.ceil(duration_seconds)),),
        )


class DeterministicAudioProcessor:
    async def master(self, request, output_path: Path):
        from app.contracts import MasterResult

        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(
            str(path) for path in request.ordered_segment_paths
        ).encode()
        output_path.write_bytes(payload)
        return MasterResult(
            duration_ms=90_000,
            sha256=hashlib.sha256(payload).hexdigest(),
            codec="mp3",
            sample_rate=44_100,
            channels=1,
            bitrate_kbps=128,
            integrated_lufs=-16.0,
            true_peak_dbtp=-1.5,
        )

    async def probe(self, path: Path, expected_sha256: str | None = None):
        from app.contracts import MasterResult

        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected_sha256 is not None and sha256 != expected_sha256:
            raise ValueError("master checksum mismatch")
        return MasterResult(
            duration_ms=90_000,
            sha256=sha256,
            codec="mp3",
            sample_rate=44_100,
            channels=1,
            bitrate_kbps=128,
            integrated_lufs=-16.0,
            true_peak_dbtp=-1.5,
        )


class _Fixture:
    def __init__(
        self,
        chapter_id: str,
        translation_run_id: str,
        translation_segment_ids: tuple[str, ...],
    ) -> None:
        self.chapter_id = chapter_id
        self.translation_run_id = translation_run_id
        self.translation_segment_ids = translation_segment_ids


def _review_chapter(db_session) -> _Fixture:
    return _chapter_with_translation(db_session, RunStatus.REVIEW)


def _approved_chapter(db_session) -> _Fixture:
    return _chapter_with_translation(db_session, RunStatus.APPROVED)


def _chapter_with_translation(db_session, status: RunStatus) -> _Fixture:
    project = Project(
        id="018f0000-0000-7000-8000-100000000001",
        title="Truyen",
        slug="truyen",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
        style_guide_text="Plain Vietnamese narration.",
    )
    db_session.add(project)
    db_session.flush()
    chapter = Chapter(
        id="018f0000-0000-7000-8000-200000000001",
        project_id=project.id,
        ordinal=1,
        source_title="Mot",
        state=ChapterState.TRANSLATION_APPROVED.value
        if status is RunStatus.APPROVED
        else ChapterState.TRANSLATION_REVIEW.value,
    )
    revision = SourceRevision(
        id="018f0000-0000-7000-8000-300000000001",
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text="source",
        normalized_sha256=_sha("source"),
        han_char_count=0,
        total_char_count=6,
        normalizer_version="nfc-v1",
    )
    db_session.add_all((chapter, revision))
    db_session.flush()
    chapter.active_source_revision_id = revision.id
    run = TranslationRun(
        id="018f0000-0000-7000-8000-400000000001",
        chapter_id=chapter.id,
        source_revision_id=revision.id,
        prompt_version="translation-v1",
        status=status.value,
        translation_text_sha256="a" * 64,
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    db_session.add(run)
    db_session.flush()
    texts = (
        "Doan mot co 42 km. "
        + "Cau van ngan giu nhip ke chuyen am ap va ro rang. " * 8,
        "Ngay 12/05/2026, nhan vat gap lai ban cu. "
        + "Giong ke cham rai va am, khong doi vai. " * 8,
        "TS. An noi rang 3 kg gao van con tren ban. "
        + "Nguoi ke giu nhip on dinh den het canh. " * 8,
    )
    segment_ids: list[str] = []
    for index, target_text in enumerate(texts):
        source = SourceSegment(
            id=f"018f0000-0000-7000-8000-50000000000{index}",
            source_revision_id=revision.id,
            segment_index=index,
            paragraph_start=index,
            paragraph_end=index,
            source_text=f"source {index}",
            source_sha256=_sha(f"source {index}"),
            segment_kind="SOURCE",
        )
        segment = TranslationSegment(
            id=f"018f0000-0000-7000-8000-60000000000{index}",
            translation_run_id=run.id,
            source_segment_id=source.id,
            target_text=target_text,
            target_sha256=_sha(target_text),
            was_cache_hit=False,
            manually_edited=False,
        )
        db_session.add_all((source, segment))
        segment_ids.append(segment.id)
    if status is RunStatus.APPROVED:
        chapter.approved_translation_run_id = run.id
    db_session.commit()
    return _Fixture(chapter.id, run.id, tuple(segment_ids))


def _voice_preset(db_session, *, provider_voice_id: str = "voice-1") -> VoicePreset:
    license_artifact = Artifact(
        id="018f0000-0000-7000-8000-700000000090",
        kind=ArtifactKind.LICENSE_SNAPSHOT.value,
        status=ArtifactStatus.READY.value,
        relative_path="models/license.txt",
        sha256="c" * 64,
        byte_size=12,
        mime_type="text/plain",
        input_hash="d" * 64,
        settings_hash="e" * 64,
    )
    preset = VoicePreset(
        id="018f0000-0000-7000-8000-700000000001",
        name="Narrator",
        provider_voice_id=provider_voice_id,
        locale="vi-VN",
        origin=VoiceOrigin.BUILT_IN.value,
        gender_label="neutral",
        region_label="local",
        speed="1.0",
        pitch="0",
        style=None,
        sample_rate=44_100,
        settings_json={"speed": "1.0", "pitch": "0"},
        model_snapshot_hash="b" * 64,
        license_snapshot_artifact_id=license_artifact.id,
        active=True,
    )
    db_session.add_all((license_artifact, preset))
    db_session.commit()
    return preset


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
