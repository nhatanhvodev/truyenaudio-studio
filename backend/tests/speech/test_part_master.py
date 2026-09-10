"""A04 - part-master, final master and SRT, offline on FakeMp3AudioProcessor.

ffmpeg is not installed on this machine (ffmpeg -version exits 1), so every
test here runs against FakeMp3AudioProcessor: the part split, the final concat
manifest, the cache keys and the SRT timeline are real workflow behaviour,
while what they cannot prove is FFmpeg/loudnorm behaviour or Vietnamese voice
quality - both reported NOT_RUN in .superpowers/sdd/task-57-report.md.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ChapterState,
    ImportKind,
    JobKind,
    JobStatus,
    QaCategory,
    QaStatus,
    RightsStatus,
    RunStatus,
    SourceType,
    MasterResult,
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
from app.modules.jobs.execution_handlers import build_synthesize_handler
from app.modules.jobs.runner import JobRunner
from app.modules.speech.workflow import (
    PART_MAX_DURATION_MS,
    PART_MAX_SEGMENTS,
    SpeechWorkflow,
    AudioApprovalBlocked,
    AudioApprovalConflict,
    _canonical_sha256,
    _master_settings_hash,
    _part_groups,
)
from app.providers.fake import FakeMp3AudioProcessor, FakeTts
from app.worker import Worker
from tests.speech.test_render_resumable import worker_db_path  # noqa: F401

PROJECT_ID = "018f0000-0000-7002-8000-0000000000a1"
CHAPTER_ID = "018f0000-0000-7002-8000-0000000000a2"
REVISION_ID = "018f0000-0000-7002-8000-0000000000a3"
RUN_ID = "018f0000-0000-7002-8000-0000000000a4"


class CountingFakeTts:
    """FakeTts with a per-segment call counter for cache assertions."""

    def __init__(self) -> None:
        self._adapter = FakeTts()
        self.calls: list[str] = []

    def capabilities(self) -> dict[str, object]:
        return self._adapter.capabilities()

    async def list_voices(self, locale: str) -> list[dict[str, object]]:
        return await self._adapter.list_voices(locale)

    async def synthesize(self, request, output_path: Path):
        self.calls.append(request.speech_segment_id)
        return await self._adapter.synthesize(request, output_path)


class TempoTts:
    """FakeTts that renders at a configurable frequency.

    FakeTts always writes the same WAV bytes, so a text-only edit would keep
    every digest identical; changing the frequency changes the audio itself,
    which is what a real narration edit does.
    """

    def __init__(self, frequency: int) -> None:
        self._adapter = FakeTts()
        self.frequency = frequency
        self.calls: list[str] = []

    def capabilities(self) -> dict[str, object]:
        return self._adapter.capabilities()

    async def list_voices(self, locale: str) -> list[dict[str, object]]:
        return await self._adapter.list_voices(locale)

    async def synthesize(self, request, output_path: Path):
        import math
        import wave

        self.calls.append(request.speech_segment_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        frames = bytearray()
        for index in range(44_100):
            sample = round(16_384 * math.sin(2 * math.pi * self.frequency * index / 44_100))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(44_100)
            wav.writeframes(bytes(frames))
        from app.contracts import SynthesisResult, Usage, UsageUnit

        digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return SynthesisResult(
            provider="fake",
            model="fake-tts",
            provider_version="1",
            duration_ms=1_000,
            sha256=digest,
            usage=(Usage(UsageUnit.AUDIO_SECOND.value, 1),),
        )


class CountingAudioProcessor:
    """FakeMp3AudioProcessor that counts master() calls."""

    def __init__(self) -> None:
        self._inner = FakeMp3AudioProcessor()
        self.master_calls = 0
        self.call_sizes: list[int] = []

    async def master(self, request, output_path: Path) -> MasterResult:
        self.master_calls += 1
        self.call_sizes.append(len(request.ordered_segment_paths))
        return await self._inner.master(request, output_path)

    async def probe(self, path: Path, expected_sha256: str | None = None) -> MasterResult:
        return await self._inner.probe(path, expected_sha256)


class ChecksumLyingAudioProcessor:
    """Masters like FakeMp3AudioProcessor but reports a wrong digest."""

    def __init__(self) -> None:
        self._inner = FakeMp3AudioProcessor()

    async def master(self, request, output_path: Path) -> MasterResult:
        result = await self._inner.master(request, output_path)
        return MasterResult(
            duration_ms=result.duration_ms,
            sha256="f" * 64,
            codec=result.codec,
            sample_rate=result.sample_rate,
            channels=result.channels,
            bitrate_kbps=result.bitrate_kbps,
            integrated_lufs=result.integrated_lufs,
            true_peak_dbtp=result.true_peak_dbtp,
        )

    async def probe(self, path: Path, expected_sha256: str | None = None) -> MasterResult:
        return await self._inner.probe(path, expected_sha256)


class FailingFinalProcessor:
    """Parts master fine; the final concat call fails like a broken ffmpeg."""

    def __init__(self) -> None:
        self._inner = FakeMp3AudioProcessor()
        self.final_calls = 0

    async def master(self, request, output_path: Path) -> MasterResult:
        names = [Path(path).name for path in request.ordered_segment_paths]
        if all(name.endswith(".mp3") for name in names):
            self.final_calls += 1
            raise RuntimeError("simulated ffmpeg failure during final concat")
        return await self._inner.master(request, output_path)

    async def probe(self, path: Path, expected_sha256: str | None = None) -> MasterResult:
        return await self._inner.probe(path, expected_sha256)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _chapter_with_segments(db_session, *, segment_count: int) -> None:
    """An approved chapter with a single-narrator plan of short segments."""
    project = Project(
        id=PROJECT_ID,
        title="Partmaster",
        slug="part-master",
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
    )
    chapter = Chapter(
        id=CHAPTER_ID,
        project_id=project.id,
        ordinal=1,
        source_title="Phan",
        state=ChapterState.TRANSLATION_APPROVED.value,
    )
    revision = SourceRevision(
        id=REVISION_ID,
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text="source",
        normalized_sha256=_sha("source"),
        han_char_count=0,
        total_char_count=6,
        normalizer_version="nfc-v1",
    )
    db_session.add(project)
    db_session.flush()
    db_session.add_all((chapter, revision))
    db_session.flush()
    chapter.active_source_revision_id = revision.id
    run = TranslationRun(
        id=RUN_ID,
        chapter_id=chapter.id,
        source_revision_id=revision.id,
        prompt_version="translation-v1",
        status=RunStatus.APPROVED.value,
        translation_text_sha256="a" * 64,
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    db_session.add(run)
    db_session.flush()
    for index in range(segment_count):
        source = SourceSegment(
            id=f"018f0000-0000-7002-8000-b{index:012x}",
            source_revision_id=revision.id,
            segment_index=index,
            paragraph_start=index,
            paragraph_end=index,
            source_text=f"source {index}",
            source_sha256=_sha(f"source {index}"),
            segment_kind="SOURCE",
        )
        target_text = f"Cau {index}: " + " ".join(["tu"] * 10)
        segment = TranslationSegment(
            id=f"018f0000-0000-7002-8000-c{index:012x}",
            translation_run_id=run.id,
            source_segment_id=source.id,
            target_text=target_text,
            target_sha256=_sha(target_text),
            was_cache_hit=False,
            manually_edited=False,
        )
        db_session.add_all((source, segment))
    chapter.approved_translation_run_id = run.id
    db_session.commit()


def _configure_plan(db_session, preset_id: str):
    return SpeechWorkflow(db_session).configure_single(CHAPTER_ID, preset_id)


def _set_segment_estimates(db_session, duration_ms: int) -> None:
    """Force part boundaries: _part_groups splits on estimated speech time."""
    db_session.query(SpeechSegment).filter_by(chapter_id=CHAPTER_ID).update(
        {SpeechSegment.estimated_duration_ms: duration_ms}
    )
    db_session.commit()


def _workflow(db_session, tmp_path: Path, tts, audio_processor=None) -> SpeechWorkflow:
    return SpeechWorkflow(
        db_session,
        tts=tts,
        audio_processor=audio_processor or FakeMp3AudioProcessor(),
        artifact_root=tmp_path / "artifacts",
        allow_fake_tts=True,
    )


def _masters(db_session, chapter_id: str, *, ready_only: bool) -> list[Artifact]:
    db_session.expire_all()
    query = db_session.query(Artifact).filter(
        Artifact.chapter_id == chapter_id,
        Artifact.kind == ArtifactKind.MASTER_MP3.value,
    )
    if ready_only:
        query = query.filter(Artifact.status == ArtifactStatus.READY.value)
    return query.order_by(Artifact.created_at, Artifact.id).all()


def _ready_parts(db_session, chapter_id: str) -> list[Artifact]:
    return [
        artifact
        for artifact in _masters(db_session, chapter_id, ready_only=True)
        if (artifact.metadata_json or {}).get("stage") == "part"
    ]


def _final_ready(db_session, chapter_id: str) -> Artifact:
    finals = [
        artifact
        for artifact in _masters(db_session, chapter_id, ready_only=True)
        if (artifact.metadata_json or {}).get("stage") == "final"
    ]
    assert len(finals) == 1, f"expected exactly one READY final, got {len(finals)}"
    return finals[0]


def _ready_srt(db_session, chapter_id: str) -> Artifact:
    db_session.expire_all()
    return (
        db_session.query(Artifact)
        .filter(
            Artifact.chapter_id == chapter_id,
            Artifact.kind == ArtifactKind.SRT.value,
            Artifact.status == ArtifactStatus.READY.value,
        )
        .one()
    )


def _speech_segments_in_order(db_session) -> list[SpeechSegment]:
    chapter = db_session.get(Chapter, CHAPTER_ID)
    return (
        db_session.query(SpeechSegment)
        .filter_by(voice_plan_id=chapter.active_voice_plan_id)
        .order_by(SpeechSegment.segment_index)
        .all()
    )


def _voice_preset(db_session) -> VoicePreset:
    license_artifact = Artifact(
        id="018f0000-0000-7002-8000-0000000000d0",
        kind=ArtifactKind.LICENSE_SNAPSHOT.value,
        status=ArtifactStatus.READY.value,
        relative_path="models/license-part.txt",
        sha256="c" * 64,
        byte_size=12,
        mime_type="text/plain",
        input_hash="d" * 64,
        settings_hash="e" * 64,
    )
    preset = VoicePreset(
        id="018f0000-0000-7002-8000-0000000000d1",
        name="Narrator",
        provider_voice_id="voice-part-1",
        locale="vi-VN",
        origin="BUILT_IN",
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


def _srt_cues(text: str) -> list[tuple[int, int, str]]:
    cues: list[tuple[int, int, str]] = []
    for block in text.strip().split("\n\n"):
        rows = block.splitlines()
        start, end = rows[1].split(" --> ")
        cues.append((_srt_ms(start), _srt_ms(end), rows[2]))
    return cues


def _srt_ms(timestamp: str) -> int:
    hours, minutes, rest = timestamp.split(":")
    seconds, ms = rest.split(",")
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(ms)


def test_part_groups_split_120_segments_into_50_50_20() -> None:
    segments = tuple(
        SpeechSegment(
            id=f"018f0000-0000-7002-8000-e{index:012x}",
            chapter_id=CHAPTER_ID,
            translation_run_id=RUN_ID,
            voice_plan_id="018f0000-0000-7002-8000-f00000000001",
            segment_index=index,
            translation_segment_id=f"018f0000-0000-7002-8000-c{index:012x}",
            narration_text=f"Cau {index}",
            narration_sha256=_sha(f"Cau {index}"),
            estimated_duration_ms=10_000,
        )
        for index in range(120)
    )

    groups = _part_groups(segments)

    assert [len(group) for group in groups] == [50, 50, 20]
    assert groups[0][0].segment_index == 0
    assert groups[0][-1].segment_index == 49
    assert groups[1][0].segment_index == 50
    assert groups[1][-1].segment_index == 99
    assert groups[2][0].segment_index == 100
    assert groups[2][-1].segment_index == 119


def test_part_groups_respect_ten_minute_budget() -> None:
    segments = tuple(
        SpeechSegment(
            id=f"018f0000-0000-7002-8000-e{index:012x}",
            chapter_id=CHAPTER_ID,
            translation_run_id=RUN_ID,
            voice_plan_id="018f0000-0000-7002-8000-f00000000001",
            segment_index=index,
            translation_segment_id=f"018f0000-0000-7002-8000-c{index:012x}",
            narration_text=f"Cau {index}",
            narration_sha256=_sha(f"Cau {index}"),
            estimated_duration_ms=120_000,  # 5 segments x 2 minutes = 10 minutes
        )
        for index in range(6)
    )

    groups = _part_groups(segments)

    assert [len(group) for group in groups] == [5, 1]
    for group in groups:
        speech_ms = sum(segment.estimated_duration_ms or 0 for segment in group)
        assert speech_ms <= PART_MAX_DURATION_MS


def test_120_segments_render_three_parts_and_final_manifest(
    db_session, tmp_path: Path
) -> None:
    _chapter_with_segments(db_session, segment_count=120)
    preset = _voice_preset(db_session)
    _configure_plan(db_session, preset.id)
    tts = CountingFakeTts()
    workflow = _workflow(db_session, tmp_path, tts)

    rendered = workflow.enqueue_render(CHAPTER_ID)

    parts = _ready_parts(db_session, CHAPTER_ID)
    assert [
        len(artifact.metadata_json["speech_segment_ids"]) for artifact in parts
    ] == [50, 50, 20]
    assert [artifact.metadata_json["part_index"] for artifact in parts] == [0, 1, 2]
    assert all(artifact.metadata_json["part_count"] == 3 for artifact in parts)
    final = _final_ready(db_session, CHAPTER_ID)
    assert final.metadata_json["stage"] == "final"
    manifest = final.metadata_json["part_sha256s"]
    assert manifest == [part.sha256 for part in parts]
    assert final.metadata_json["total_segments"] == 120
    assert len(rendered.segment_ids) == 120
    assert rendered.part_artifact_ids == tuple(part.id for part in parts)
    assert rendered.master_artifact_id == final.id
    assert final.input_hash == _canonical_sha256(
        {
            "translation_run": RUN_ID,
            "plan": final.metadata_json["voice_plan_sha256"],
            "stage": "final-master",
            "part_count": 3,
            "total_segments": 120,
            "parts": tuple(manifest),
            "part_inputs": tuple(final.metadata_json["part_input_hashes"]),
        }
    )
    assert final.settings_hash == _master_settings_hash()
    assert tts.calls == list(rendered.segment_ids)  # ordered, nothing skipped


def test_single_short_chapter_still_has_one_part_and_final(
    db_session, tmp_path: Path
) -> None:
    _chapter_with_segments(db_session, segment_count=3)
    preset = _voice_preset(db_session)
    _configure_plan(db_session, preset.id)
    workflow = _workflow(db_session, tmp_path, CountingFakeTts())

    rendered = workflow.enqueue_render(CHAPTER_ID)

    parts = _ready_parts(db_session, CHAPTER_ID)
    assert len(parts) == 1
    assert rendered.part_artifact_ids == (parts[0].id,)
    final = _final_ready(db_session, CHAPTER_ID)
    assert final.metadata_json["part_count"] == 1
    assert final.metadata_json["part_sha256s"] == [parts[0].sha256]
    assert final.metadata_json["total_segments"] == 3


def test_editing_one_segment_rebuilds_only_its_part(
    db_session, tmp_path: Path
) -> None:
    _chapter_with_segments(db_session, segment_count=120)
    preset = _voice_preset(db_session)
    _configure_plan(db_session, preset.id)
    tts = CountingFakeTts()
    workflow = _workflow(db_session, tmp_path, tts)
    first = workflow.enqueue_render(CHAPTER_ID)
    first_part_ids = list(first.part_artifact_ids)

    edited = db_session.get(SpeechSegment, first.segment_ids[60])
    edited.narration_text = "Cau sau khi sua van giu duoc nhip ke chuyen."
    edited.narration_sha256 = _sha(edited.narration_text)
    db_session.commit()

    processor = CountingAudioProcessor()
    workflow = _workflow(db_session, tmp_path, tts, audio_processor=processor)
    second = workflow.enqueue_render(CHAPTER_ID)

    # Only the segment itself was re-synthesized.
    assert second.rendered_segment_ids == (first.segment_ids[60],)
    assert len(tts.calls) == 121
    # Exactly one part was re-mastered, holding 50 segments (the edited one's part).
    assert processor.master_calls == 2  # one part + the final concat
    assert processor.call_sizes == [50, 3]
    rebuilt = _ready_parts(db_session, CHAPTER_ID)
    assert len(rebuilt) == 3
    rebuilt_ids = {artifact.id for artifact in rebuilt}
    reused_ids = set(first_part_ids) & rebuilt_ids
    assert len(reused_ids) == 2  # parts 1 and 3 kept their artifacts
    superseded_parts = [
        artifact
        for artifact in _masters(db_session, CHAPTER_ID, ready_only=False)
        if artifact.status == ArtifactStatus.SUPERSEDED.value
        and (artifact.metadata_json or {}).get("stage") == "part"
    ]
    assert len(superseded_parts) == 1
    assert first.segment_ids[60] in superseded_parts[0].metadata_json[
        "speech_segment_ids"
    ]
    assert (tmp_path / "artifacts" / superseded_parts[0].relative_path).is_file()
    # The final was rebuilt because part 2 changed.
    assert second.master_artifact_id != first.master_artifact_id


def test_full_second_pass_reuses_every_part_and_final(
    db_session, tmp_path: Path
) -> None:
    _chapter_with_segments(db_session, segment_count=60)
    preset = _voice_preset(db_session)
    _configure_plan(db_session, preset.id)
    workflow = _workflow(db_session, tmp_path, CountingFakeTts())
    first = workflow.enqueue_render(CHAPTER_ID)

    processor = CountingAudioProcessor()
    workflow = _workflow(
        db_session, tmp_path, CountingFakeTts(), audio_processor=processor
    )
    second = workflow.enqueue_render(CHAPTER_ID)

    assert processor.master_calls == 0
    assert second.master_artifact_id == first.master_artifact_id
    assert second.part_artifact_ids == first.part_artifact_ids


def test_lying_checksum_never_marks_part_or_final_ready(
    db_session, tmp_path: Path
) -> None:
    _chapter_with_segments(db_session, segment_count=6)
    preset = _voice_preset(db_session)
    _configure_plan(db_session, preset.id)
    tts = CountingFakeTts()
    workflow = _workflow(
        db_session, tmp_path, tts, audio_processor=ChecksumLyingAudioProcessor()
    )

    with pytest.raises(ValueError, match="MASTER_CHECKSUM_MISMATCH"):
        workflow.enqueue_render(CHAPTER_ID)

    assert _masters(db_session, CHAPTER_ID, ready_only=False) == []
    db_session.expire_all()
    ready_segments = (
        db_session.query(Artifact)
        .filter(
            Artifact.chapter_id == CHAPTER_ID,
            Artifact.kind == ArtifactKind.TTS_SEGMENT.value,
            Artifact.status == ArtifactStatus.READY.value,
        )
        .all()
    )
    assert len(ready_segments) == 6  # no segment audio was lost
    leftover_parts = sorted(
        path
        for path in (tmp_path / "artifacts").rglob("*")
        if path.is_file() and path.name.endswith(".mp3")
    )
    assert leftover_parts == []  # the bad part file was destroyed


def test_checksum_failure_recovers_on_a_clean_second_render(
    db_session, tmp_path: Path
) -> None:
    _chapter_with_segments(db_session, segment_count=6)
    preset = _voice_preset(db_session)
    _configure_plan(db_session, preset.id)
    tts = CountingFakeTts()
    workflow = _workflow(
        db_session, tmp_path, tts, audio_processor=ChecksumLyingAudioProcessor()
    )
    with pytest.raises(ValueError, match="MASTER_CHECKSUM_MISMATCH"):
        workflow.enqueue_render(CHAPTER_ID)

    workflow = _workflow(db_session, tmp_path, tts)
    rendered = workflow.enqueue_render(CHAPTER_ID)

    assert rendered.master_artifact_id
    final = _final_ready(db_session, CHAPTER_ID)
    assert final.metadata_json["total_segments"] == 6
    # The failed pass synthesized all 6; the recovery pass reuses every segment
    # from cache instead of paying for them again - nothing was lost.
    assert len(tts.calls) == 6


def test_ffmpeg_failure_during_final_keeps_old_master_and_segments(
    db_session, tmp_path: Path
) -> None:
    _chapter_with_segments(db_session, segment_count=6)
    preset = _voice_preset(db_session)
    _configure_plan(db_session, preset.id)
    tts = TempoTts(440)
    workflow = _workflow(db_session, tmp_path, tts)
    first = workflow.enqueue_render(CHAPTER_ID)
    first_master = db_session.get(Artifact, first.master_artifact_id)
    first_master_path = tmp_path / "artifacts" / first_master.relative_path
    assert first_master_path.is_file()
    # Change the audio itself (tempo) so the part and the final must really be
    # rebuilt; a text-only edit would keep the fake WAV bytes identical.
    tts.frequency = 660
    edited = db_session.get(SpeechSegment, first.segment_ids[0])
    edited.narration_text = "Cau bien doi truoc lan thu voi ffmpeg hong."
    edited.narration_sha256 = _sha(edited.narration_text)
    db_session.commit()

    processor = FailingFinalProcessor()
    workflow = _workflow(
        db_session, tmp_path, CountingFakeTts(), audio_processor=processor
    )
    with pytest.raises(RuntimeError, match="ffmpeg failure"):
        workflow.enqueue_render(CHAPTER_ID)

    db_session.expire_all()
    still_ready = db_session.get(Artifact, first.master_artifact_id)
    assert still_ready.status == ArtifactStatus.READY.value
    assert first_master_path.is_file()  # the old master file was not deleted
    ready_segments = (
        db_session.query(Artifact)
        .filter(
            Artifact.chapter_id == CHAPTER_ID,
            Artifact.kind == ArtifactKind.TTS_SEGMENT.value,
            Artifact.status == ArtifactStatus.READY.value,
        )
        .all()
    )
    assert len(ready_segments) == 6
    # A fresh render converges to a new READY final on top of the old master.
    workflow = _workflow(db_session, tmp_path, CountingFakeTts())
    rendered = workflow.enqueue_render(CHAPTER_ID)
    assert rendered.master_artifact_id != first.master_artifact_id
    assert _final_ready(db_session, CHAPTER_ID).status == ArtifactStatus.READY.value


def test_srt_alignment_across_part_boundary_uses_real_part_duration(
    db_session, tmp_path: Path
) -> None:
    _chapter_with_segments(db_session, segment_count=6)
    preset = _voice_preset(db_session)
    _configure_plan(db_session, preset.id)
    # 3 x 160000 ms fit one part, the 4th would exceed 600000 ms: parts [3, 3].
    _set_segment_estimates(db_session, 160_000)
    workflow = _workflow(db_session, tmp_path, CountingFakeTts())
    rendered = workflow.enqueue_render(CHAPTER_ID)

    parts = _ready_parts(db_session, CHAPTER_ID)
    assert [artifact.metadata_json["part_index"] for artifact in parts] == [0, 1]
    part0 = parts[0]
    srt = _ready_srt(db_session, CHAPTER_ID)
    cues = _srt_cues(
        (tmp_path / "artifacts" / srt.relative_path).read_text(encoding="utf-8")
    )
    segments = _speech_segments_in_order(db_session)
    assert len(cues) == 6
    first_of_part2 = segments[3]
    cue = cues[3]
    assert cue[2] == first_of_part2.narration_text
    pause_ms = segments[2].pause_after_ms
    # The first segment of part 2 starts at the real mastered duration of part 1
    # plus the pause after the last segment of part 1 - never an estimate.
    assert cue[0] == part0.duration_ms + pause_ms
    assert rendered.srt_artifact_id == srt.id


def test_rerender_supersedes_old_master_and_srt_without_deleting_files(
    db_session, tmp_path: Path
) -> None:
    _chapter_with_segments(db_session, segment_count=3)
    preset = _voice_preset(db_session)
    _configure_plan(db_session, preset.id)
    tts = TempoTts(440)
    workflow = _workflow(db_session, tmp_path, tts)
    first = workflow.enqueue_render(CHAPTER_ID)
    first_master = db_session.get(Artifact, first.master_artifact_id)
    first_srt = _ready_srt(db_session, CHAPTER_ID)
    first_master_path = tmp_path / "artifacts" / first_master.relative_path
    first_srt_path = tmp_path / "artifacts" / first_srt.relative_path

    tts.frequency = 660  # different narration audio, same text length
    edited = db_session.get(SpeechSegment, first.segment_ids[1])
    edited.narration_text = "Cau da bien doi de bat buoc master moi."
    edited.narration_sha256 = _sha(edited.narration_text)
    db_session.commit()
    workflow.enqueue_render(CHAPTER_ID)

    db_session.expire_all()
    old_master = db_session.get(Artifact, first.master_artifact_id)
    old_srt = db_session.get(Artifact, first_srt.id)
    assert old_master.status == ArtifactStatus.SUPERSEDED.value
    assert old_srt.status == ArtifactStatus.SUPERSEDED.value
    assert first_master_path.is_file()  # the old master file is kept
    assert first_srt_path.is_file()
    new_final = _final_ready(db_session, CHAPTER_ID)
    assert new_final.id != first.master_artifact_id
    new_srt = _ready_srt(db_session, CHAPTER_ID)
    assert new_srt.id != first_srt.id


async def test_part_resume_after_worker_synth_then_inline_master(
    # `worker_db_path` is an imported fixture (see the import above), so it is
    # requested by name only - redeclaring it here shadowed the fixture (F811).
    worker_db_path,  # noqa: F811 - the imported fixture is requested by name
    migrated_engine: Engine,
    db_session,
    tmp_path: Path,
) -> None:
    """SYNTHESIZE under the real worker, then master inline: parts land cleanly."""
    _chapter_with_segments(db_session, segment_count=6)
    preset = _voice_preset(db_session)
    plan = _configure_plan(db_session, preset.id)
    _set_segment_estimates(db_session, 160_000)  # parts [3, 3]
    tts = CountingFakeTts()
    runner = JobRunner(migrated_engine)
    handler = build_synthesize_handler(
        tts=tts,
        audio_processor=FakeMp3AudioProcessor(),
        allow_fake_tts=True,
    )
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    worker = Worker(
        runner,
        handlers={JobKind.SYNTHESIZE: handler},
        artifact_root=artifact_root,
    )

    job = runner.enqueue(
        JobKind.SYNTHESIZE,
        PROJECT_ID,
        CHAPTER_ID,
        "part-resume-1",
        plan={
            "projectId": PROJECT_ID,
            "profileId": "local-tts",
            "cloudConsentId": "",
            "budgetAuthorizationId": "",
            "translationRunId": RUN_ID,
            "voicePlanId": plan.id,
        },
    )
    await worker.run_once()

    assert runner.get(job.id).status is JobStatus.SUCCEEDED

    workflow = SpeechWorkflow(
        db_session,
        tts=tts,
        audio_processor=FakeMp3AudioProcessor(),
        artifact_root=artifact_root,
        allow_fake_tts=True,
    )
    # The inline render is synchronous (asyncio.run inside); run it off the
    # test's event loop exactly like the worker runs it in a thread.
    rendered = await asyncio.to_thread(workflow.enqueue_render, CHAPTER_ID)

    assert rendered.master_artifact_id
    parts = _ready_parts(db_session, CHAPTER_ID)
    assert [artifact.metadata_json["part_index"] for artifact in parts] == [0, 1]
    assert [artifact.metadata_json["part_count"] for artifact in parts] == [2, 2]
    assert tts.calls == list(rendered.segment_ids)
    final = _final_ready(db_session, CHAPTER_ID)
    assert final.metadata_json["total_segments"] == 6


def test_clipping_segment_blocks_audio_approval(
    db_session, tmp_path: Path
) -> None:
    """G-CORE: a clipping segment produces an open QA issue that blocks approval."""
    from tests.speech.test_single_narrator import WavTts

    _chapter_with_segments(db_session, segment_count=6)
    preset = _voice_preset(db_session)
    _configure_plan(db_session, preset.id)
    workflow = _workflow(db_session, tmp_path, WavTts("clipped"))
    rendered = workflow.enqueue_render(CHAPTER_ID)

    issues = (
        db_session.query(QaIssue)
        .filter(QaIssue.category == QaCategory.CLIPPING.value)
        .all()
    )
    assert issues
    assert all(issue.status == QaStatus.OPEN.value for issue in issues)
    with pytest.raises(AudioApprovalBlocked):
        workflow.approve_audio(
            CHAPTER_ID,
            rendered.master_artifact_id,
            expected_sha256=rendered.master_sha256,
        )


def test_part_artifact_is_not_approvable(db_session, tmp_path: Path) -> None:
    _chapter_with_segments(db_session, segment_count=3)
    preset = _voice_preset(db_session)
    _configure_plan(db_session, preset.id)
    workflow = _workflow(db_session, tmp_path, CountingFakeTts())
    rendered = workflow.enqueue_render(CHAPTER_ID)

    part_id = rendered.part_artifact_ids[0]
    part = db_session.get(Artifact, part_id)
    with pytest.raises(AudioApprovalConflict, match="PART_ARTIFACT_NOT_APPROVABLE"):
        workflow.approve_audio(CHAPTER_ID, part_id, expected_sha256=part.sha256)

    approved = workflow.approve_audio(
        CHAPTER_ID,
        rendered.master_artifact_id,
        expected_sha256=rendered.master_sha256,
    )
    assert approved.status == "APPROVED"


def test_part_limits_are_pinned() -> None:
    assert PART_MAX_SEGMENTS == 50
    assert PART_MAX_DURATION_MS == 600_000
