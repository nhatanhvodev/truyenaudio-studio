from __future__ import annotations

from collections.abc import Callable
import asyncio
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    AudioProcessor,
    ChapterState,
    MasterRequest,
    MasterResult,
    OperationContext,
    QaCategory,
    QaSeverity,
    QaStatus,
    RunStatus,
    SynthesisRequest,
    TtsAdapter,
    VoiceMode,
    new_id,
)
from app.db.base import utc_now
from app.db.models import (
    Artifact,
    AuditEvent,
    Chapter,
    QaIssue,
    SpeechSegment,
    TranslationRun,
    TranslationSegment,
    VoicePlan,
    VoicePreset,
    VoiceRole,
)
from app.modules.audio.qa import AudioIssueDraft, run_master_qa, run_premaster_qa
from app.modules.projects.state_machine import next_state
from app.modules.speech.narration import (
    derive_narration,
    estimate_duration_ms,
    narration_chunks,
)
from app.providers.fake import FakeTts
from app.providers.ffmpeg_audio import FFmpegAudioProcessor


ZERO_HASH = "0" * 64
SPEECH_SETTINGS_VERSION = "speech-single-v1"


class TranslationApprovalRequired(Exception):
    """Raised when speech work is requested before translation approval."""


class VoicePlanRequired(Exception):
    """Raised when rendering is requested before configuring a voice plan."""


class AudioApprovalBlocked(Exception):
    """Raised when major or critical audio QA issues are still open."""


class AudioApprovalConflict(Exception):
    """Raised when the requested master artifact is stale or invalid."""


@dataclass(frozen=True)
class VoiceRoleView:
    id: str
    role_key: str
    display_name: str
    voice_preset_id: str
    is_narrator: bool


@dataclass(frozen=True)
class SpeechSegmentView:
    id: str
    translation_segment_id: str
    narration_text: str
    narration_sha256: str
    narration_diff: str
    estimated_duration_ms: int
    synthesis_cache_key: str | None


@dataclass(frozen=True)
class VoicePlanView:
    id: str
    chapter_id: str
    mode: VoiceMode
    narrator_preset_id: str
    plan_sha256: str
    roles: tuple[VoiceRoleView, ...]
    segments: tuple[SpeechSegmentView, ...]


@dataclass(frozen=True)
class RenderedChapterView:
    chapter_id: str
    segment_ids: tuple[str, ...]
    reused_segment_ids: tuple[str, ...]
    rendered_segment_ids: tuple[str, ...]
    tts_artifact_ids: tuple[str, ...]
    master_artifact_id: str
    master_sha256: str
    srt_artifact_id: str


@dataclass(frozen=True)
class AudioApprovalView:
    chapter_id: str
    master_artifact_id: str
    status: str


class SpeechWorkflow:
    def __init__(
        self,
        session: Session,
        *,
        tts: TtsAdapter | None = None,
        audio_processor: AudioProcessor | None = None,
        artifact_root: Path | None = None,
        id_factory: Callable[[], str] = new_id,
    ) -> None:
        self.session = session
        self.tts = tts or FakeTts()
        self.audio_processor = audio_processor or FFmpegAudioProcessor()
        self.artifact_root = Path(artifact_root or Path("data") / "artifacts")
        self.id_factory = id_factory

    def preview(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError("voice preview is owned by VoiceCatalog")

    def configure_single(self, chapter_id: str, preset_id: str) -> VoicePlanView:
        chapter = self._chapter(chapter_id)
        run = self._approved_run(chapter)
        preset = self._voice_preset(preset_id)
        revision_no = self._next_plan_revision(chapter.id)
        role_id = self.id_factory()
        plan = VoicePlan(
            id=self.id_factory(),
            chapter_id=chapter.id,
            revision_no=revision_no,
            mode=VoiceMode.SINGLE_NARRATOR.value,
            narrator_preset_id=preset.id,
            plan_sha256=ZERO_HASH,
        )
        self.session.add(plan)
        self.session.flush()
        role = VoiceRole(
            id=role_id,
            voice_plan_id=plan.id,
            role_key="narrator",
            display_name="Narrator",
            voice_preset_id=preset.id,
            is_narrator=True,
        )
        self.session.add(role)
        self.session.flush()

        segment_index = 0
        for translation_segment in self._translation_segments(run.id):
            revision = derive_narration(translation_segment.target_text)
            for chunk in narration_chunks(revision.text):
                chunk_revision = derive_narration(chunk)
                self.session.add(
                    SpeechSegment(
                        id=self.id_factory(),
                        chapter_id=chapter.id,
                        translation_run_id=run.id,
                        voice_plan_id=plan.id,
                        segment_index=segment_index,
                        translation_segment_id=translation_segment.id,
                        role_id=role.id,
                        narration_text=chunk_revision.text,
                        narration_sha256=chunk_revision.sha256,
                        pause_before_ms=0,
                        pause_after_ms=500,
                        pronunciation_revision_hash=chunk_revision.pronunciation_hash,
                        estimated_duration_ms=estimate_duration_ms(chunk_revision.text),
                    )
                )
                segment_index += 1

        self.session.flush()
        plan.plan_sha256 = self._plan_hash(plan.id, run)
        chapter.active_voice_plan_id = plan.id
        chapter.state = next_state(chapter.state, ChapterState.VOICE_CONFIGURED).value
        self.session.commit()
        return self._plan_view(plan.id)

    def enqueue_render(self, chapter_id: str) -> RenderedChapterView:
        return self._render(chapter_id, force_segment_ids=frozenset())

    def regenerate_segments(
        self,
        chapter_id: str,
        segment_ids: tuple[str, ...],
    ) -> RenderedChapterView:
        return self._render(chapter_id, force_segment_ids=frozenset(segment_ids))

    def approve_audio(
        self,
        chapter_id: str,
        master_artifact_id: str,
        *,
        expected_sha256: str,
    ) -> AudioApprovalView:
        chapter = self._chapter(chapter_id)
        artifact = self.session.get(Artifact, master_artifact_id)
        if artifact is None or artifact.chapter_id != chapter.id:
            raise ValueError("MASTER_ARTIFACT_NOT_FOUND")
        if artifact.kind != ArtifactKind.MASTER_MP3.value:
            raise AudioApprovalConflict("MASTER_ARTIFACT_REQUIRED")
        if artifact.status != ArtifactStatus.READY.value:
            raise AudioApprovalConflict("MASTER_ARTIFACT_NOT_READY")
        if artifact.sha256 != expected_sha256:
            raise AudioApprovalConflict("MASTER_HASH_MISMATCH")
        master_path = self._artifact_path(artifact.relative_path)
        probe = asyncio.run(self.audio_processor.probe(master_path, expected_sha256))
        if probe.sha256 != artifact.sha256:
            raise AudioApprovalConflict("MASTER_PROBE_HASH_MISMATCH")
        blockers = self._open_audio_blockers(chapter.id)
        if blockers:
            raise AudioApprovalBlocked("AUDIO_QA_BLOCKERS_OPEN")

        previous_artifact_id = chapter.approved_master_artifact_id
        before_hash = None
        if previous_artifact_id is not None:
            previous = self.session.get(Artifact, previous_artifact_id)
            before_hash = previous.sha256 if previous is not None else None
        chapter.approved_master_artifact_id = artifact.id
        chapter.audio_approved_at = utc_now()
        chapter.state = next_state(chapter.state, ChapterState.READY_TO_EXPORT).value
        self.session.add(
            AuditEvent(
                id=self.id_factory(),
                actor="LOCAL_OWNER",
                action="APPROVE_AUDIO",
                entity_type="Artifact",
                entity_id=artifact.id,
                before_hash=before_hash,
                after_hash=artifact.sha256,
                redacted_details=json.dumps(
                    {
                        "chapter_id": chapter.id,
                        "artifact_id": artifact.id,
                        "previous_artifact_id": previous_artifact_id,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
        self.session.commit()
        return AudioApprovalView(
            chapter_id=chapter.id,
            master_artifact_id=artifact.id,
            status="APPROVED",
        )

    def _render(
        self,
        chapter_id: str,
        *,
        force_segment_ids: frozenset[str],
    ) -> RenderedChapterView:
        chapter = self._chapter(chapter_id)
        if chapter.active_voice_plan_id is None:
            raise VoicePlanRequired("VOICE_PLAN_REQUIRED")
        plan = self.session.get(VoicePlan, chapter.active_voice_plan_id)
        if plan is None:
            raise VoicePlanRequired("VOICE_PLAN_REQUIRED")
        run = self._approved_run(chapter)
        preset = self._voice_preset(plan.narrator_preset_id)
        segments = self._speech_segments(plan.id)
        if not segments:
            raise VoicePlanRequired("SPEECH_SEGMENTS_REQUIRED")

        if chapter.state == ChapterState.VOICE_CONFIGURED.value:
            chapter.state = next_state(chapter.state, ChapterState.TTS_QUEUED).value
        if chapter.state == ChapterState.TTS_QUEUED.value:
            chapter.state = next_state(chapter.state, ChapterState.SYNTHESIZING).value

        rendered_segment_ids: list[str] = []
        reused_segment_ids: list[str] = []
        artifact_ids: list[str] = []
        audio_paths: list[Path] = []
        durations: list[int] = []
        settings_hash = self._settings_hash(preset)
        for segment in segments:
            cache_key = self._synthesis_cache_key(segment, preset)
            segment.synthesis_cache_key = cache_key
            cached = None
            if segment.id not in force_segment_ids:
                cached = self._ready_artifact(
                    ArtifactKind.TTS_SEGMENT,
                    cache_key,
                    settings_hash,
                )
            if cached is not None:
                reused_segment_ids.append(segment.id)
                artifact_ids.append(cached.id)
                audio_paths.append(self._artifact_path(cached.relative_path))
                durations.append(cached.duration_ms or segment.estimated_duration_ms or 0)
                continue

            self._supersede_ready_artifacts(
                ArtifactKind.TTS_SEGMENT,
                cache_key,
                settings_hash,
            )
            artifact_id = self.id_factory()
            relative_path = f"audio/{chapter.id}/segments/{artifact_id}.wav"
            output_path = self._artifact_path(relative_path)
            result = asyncio.run(self.tts.synthesize(self._synthesis_request(segment, preset, cache_key), output_path))
            actual_sha256 = _sha256_file(output_path)
            if actual_sha256 != result.sha256:
                output_path.unlink(missing_ok=True)
                raise ValueError("TTS_CHECKSUM_MISMATCH")
            artifact = self._add_artifact(
                artifact_id=artifact_id,
                chapter_id=chapter.id,
                kind=ArtifactKind.TTS_SEGMENT,
                relative_path=relative_path,
                sha256=result.sha256,
                byte_size=output_path.stat().st_size,
                mime_type="audio/wav",
                duration_ms=result.duration_ms,
                input_hash=cache_key,
                settings_hash=settings_hash,
                metadata={"speech_segment_id": segment.id},
            )
            rendered_segment_ids.append(segment.id)
            artifact_ids.append(artifact.id)
            audio_paths.append(output_path)
            durations.append(result.duration_ms)

        master = self._master(chapter, run, plan, tuple(audio_paths), tuple(segments))
        srt = self._write_srt(chapter, master.result, tuple(segments), tuple(durations))
        chapter.state = ChapterState.AUDIO_REVIEW.value
        self.session.commit()
        return RenderedChapterView(
            chapter_id=chapter.id,
            segment_ids=tuple(segment.id for segment in segments),
            reused_segment_ids=tuple(reused_segment_ids),
            rendered_segment_ids=tuple(rendered_segment_ids),
            tts_artifact_ids=tuple(artifact_ids),
            master_artifact_id=master.artifact.id,
            master_sha256=master.artifact.sha256,
            srt_artifact_id=srt.id,
        )

    def _master(
        self,
        chapter: Chapter,
        run: TranslationRun,
        plan: VoicePlan,
        audio_paths: tuple[Path, ...],
        segments: tuple[SpeechSegment, ...],
    ) -> _MasterWrite:
        input_hash = _canonical_sha256(
            {
                "translation_run": run.id,
                "plan": plan.plan_sha256,
                "segments": [_sha256_file(path) for path in audio_paths],
                "pauses": [segment.pause_after_ms for segment in segments],
            }
        )
        settings_hash = _canonical_sha256(
            {
                "kind": "master-mp3",
                "sample_rate": 44_100,
                "channels": 1,
                "bitrate": "128k",
                "loudnorm": {"I": -16, "TP": -1.5, "LRA": 11},
                "id3v2": "2.3",
            }
        )
        self._supersede_ready_artifacts(ArtifactKind.MASTER_MP3, input_hash, settings_hash)
        artifact_id = self.id_factory()
        relative_path = f"audio/{chapter.id}/masters/{artifact_id}.mp3"
        output_path = self._artifact_path(relative_path)
        result = asyncio.run(
            self.audio_processor.master(
                MasterRequest(
                    operation_id=f"master:{chapter.id}:{artifact_id}",
                    ordered_segment_paths=audio_paths,
                    pause_after_ms=tuple(segment.pause_after_ms for segment in segments),
                    metadata={
                        "title": chapter.translated_title or chapter.source_title or "Chapter",
                        "album": chapter.project_id,
                    },
                    sample_rate=44_100,
                ),
                output_path,
            )
        )
        premaster_issues = run_premaster_qa(
            audio_paths,
            tuple(segment.id for segment in segments),
        )
        actual_sha256 = _sha256_file(output_path)
        if actual_sha256 != result.sha256:
            output_path.unlink(missing_ok=True)
            raise ValueError("MASTER_CHECKSUM_MISMATCH")
        artifact = self._add_artifact(
            artifact_id=artifact_id,
            chapter_id=chapter.id,
            kind=ArtifactKind.MASTER_MP3,
            relative_path=relative_path,
            sha256=result.sha256,
            byte_size=output_path.stat().st_size,
            mime_type="audio/mpeg",
            duration_ms=result.duration_ms,
            input_hash=input_hash,
            settings_hash=settings_hash,
            metadata={
                "codec": result.codec,
                "sample_rate": result.sample_rate,
                "channels": result.channels,
                "bitrate_kbps": result.bitrate_kbps,
                "integrated_lufs": result.integrated_lufs,
                "true_peak_dbtp": result.true_peak_dbtp,
            },
        )
        self._replace_audio_qa(chapter.id, result, premaster_issues)
        return _MasterWrite(artifact=artifact, result=result)

    def _write_srt(
        self,
        chapter: Chapter,
        master: MasterResult,
        segments: tuple[SpeechSegment, ...],
        durations: tuple[int, ...],
    ) -> Artifact:
        input_hash = _canonical_sha256(
            {
                "master": master.sha256,
                "segments": [segment.narration_sha256 for segment in segments],
                "version": "srt-v1",
            }
        )
        settings_hash = _canonical_sha256({"format": "srt", "max_end_ms": master.duration_ms})
        self._supersede_ready_artifacts(ArtifactKind.SRT, input_hash, settings_hash)
        artifact_id = self.id_factory()
        relative_path = f"audio/{chapter.id}/subtitles/{artifact_id}.srt"
        output_path = self._artifact_path(relative_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(_srt_text(segments, durations, master.duration_ms), encoding="utf-8")
        return self._add_artifact(
            artifact_id=artifact_id,
            chapter_id=chapter.id,
            kind=ArtifactKind.SRT,
            relative_path=relative_path,
            sha256=_sha256_file(output_path),
            byte_size=output_path.stat().st_size,
            mime_type="application/x-subrip",
            duration_ms=master.duration_ms,
            input_hash=input_hash,
            settings_hash=settings_hash,
            metadata={"master_sha256": master.sha256},
        )

    def _synthesis_request(
        self,
        segment: SpeechSegment,
        preset: VoicePreset,
        cache_key: str,
    ) -> SynthesisRequest:
        return SynthesisRequest(
            context=OperationContext(
                operation_id=f"tts:{segment.chapter_id}:{segment.id}",
                cache_key=cache_key,
                timeout_seconds=120,
                estimated_units=len(segment.narration_text),
                budget_authorization_id=None,
                cloud_consent_id=None,
            ),
            speech_segment_id=segment.id,
            narration_text=segment.narration_text,
            locale=preset.locale,
            voice_id=preset.provider_voice_id or preset.id,
            speed=preset.speed,
            pitch=preset.pitch,
            style=preset.style,
            sample_rate=preset.sample_rate,
        )

    def _replace_audio_qa(
        self,
        chapter_id: str,
        probe: MasterResult,
        premaster_issues: tuple[AudioIssueDraft, ...],
    ) -> None:
        for issue in self.session.scalars(
            select(QaIssue).where(
                QaIssue.chapter_id == chapter_id,
                QaIssue.translation_run_id.is_(None),
                QaIssue.rule_or_model == "audio-qa-v1",
            )
        ):
            issue.status = QaStatus.FIXED.value
            issue.resolved_note = "Replaced by latest audio QA run."
            issue.resolved_at = utc_now()
        for draft in (*premaster_issues, *run_master_qa(probe)):
            self.session.add(
                QaIssue(
                    id=self.id_factory(),
                    chapter_id=chapter_id,
                    speech_segment_id=draft.speech_segment_id,
                    category=draft.category.value,
                    severity=draft.severity.value,
                    status=QaStatus.OPEN.value,
                    evidence=draft.evidence,
                    suggestion=draft.suggestion,
                    rule_or_model=draft.rule_or_model,
                )
            )

    def _open_audio_blockers(self, chapter_id: str) -> tuple[QaIssue, ...]:
        audio_categories = (
            QaCategory.TTS_LENGTH.value,
            QaCategory.AUDIO_TECHNICAL.value,
            QaCategory.PRONUNCIATION.value,
            QaCategory.SILENCE.value,
            QaCategory.CLIPPING.value,
        )
        return tuple(
            self.session.scalars(
                select(QaIssue).where(
                    QaIssue.chapter_id == chapter_id,
                    QaIssue.category.in_(audio_categories),
                    QaIssue.status == QaStatus.OPEN.value,
                    QaIssue.severity.in_(
                        (QaSeverity.MAJOR.value, QaSeverity.CRITICAL.value)
                    ),
                )
            )
        )

    def _add_artifact(
        self,
        *,
        artifact_id: str,
        chapter_id: str,
        kind: ArtifactKind,
        relative_path: str,
        sha256: str,
        byte_size: int,
        mime_type: str,
        duration_ms: int | None,
        input_hash: str,
        settings_hash: str,
        metadata: dict[str, object],
    ) -> Artifact:
        artifact = Artifact(
            id=artifact_id,
            chapter_id=chapter_id,
            kind=kind.value,
            status=ArtifactStatus.READY.value,
            relative_path=relative_path,
            sha256=sha256,
            byte_size=byte_size,
            mime_type=mime_type,
            duration_ms=duration_ms,
            producer="truyenaudio-studio",
            producer_version=SPEECH_SETTINGS_VERSION,
            input_hash=input_hash,
            settings_hash=settings_hash,
            metadata_json=metadata,
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact

    def _ready_artifact(
        self,
        kind: ArtifactKind,
        input_hash: str,
        settings_hash: str,
    ) -> Artifact | None:
        artifact = self.session.scalar(
            select(Artifact)
            .where(
                Artifact.kind == kind.value,
                Artifact.input_hash == input_hash,
                Artifact.settings_hash == settings_hash,
                Artifact.status == ArtifactStatus.READY.value,
            )
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        )
        if artifact is None:
            return None
        path = self._artifact_path(artifact.relative_path)
        if not path.is_file() or _sha256_file(path) != artifact.sha256:
            artifact.status = ArtifactStatus.CORRUPT.value
            self.session.flush()
            return None
        return artifact

    def _supersede_ready_artifacts(
        self,
        kind: ArtifactKind,
        input_hash: str,
        settings_hash: str,
    ) -> None:
        for artifact in self.session.scalars(
            select(Artifact).where(
                Artifact.kind == kind.value,
                Artifact.input_hash == input_hash,
                Artifact.settings_hash == settings_hash,
                Artifact.status == ArtifactStatus.READY.value,
            )
        ):
            artifact.status = ArtifactStatus.SUPERSEDED.value
        self.session.flush()

    def _synthesis_cache_key(self, segment: SpeechSegment, preset: VoicePreset) -> str:
        capabilities = self.tts.capabilities()
        return _canonical_sha256(
            {
                "narration": segment.narration_sha256,
                "provider": str(capabilities.get("provider") or "unknown"),
                "model": str(capabilities.get("model") or "unknown"),
                "voice": preset.provider_voice_id or preset.id,
                "settings": self._settings_hash(preset),
                "pronunciation": segment.pronunciation_revision_hash or ZERO_HASH,
            }
        )

    def _settings_hash(self, preset: VoicePreset) -> str:
        return _canonical_sha256(
            {
                "preset_id": preset.id,
                "speed": preset.speed,
                "pitch": preset.pitch,
                "style": preset.style,
                "sample_rate": preset.sample_rate,
                "settings": preset.settings_json or {},
                "model_snapshot_hash": preset.model_snapshot_hash or ZERO_HASH,
            }
        )

    def _plan_hash(self, plan_id: str, run: TranslationRun) -> str:
        segments = self._speech_segments(plan_id)
        return _canonical_sha256(
            {
                "mode": VoiceMode.SINGLE_NARRATOR.value,
                "translation_run_id": run.id,
                "translation_hash": run.translation_text_sha256,
                "segments": [
                    {
                        "id": segment.id,
                        "narration_sha256": segment.narration_sha256,
                        "pronunciation_hash": segment.pronunciation_revision_hash,
                    }
                    for segment in segments
                ],
            }
        )

    def _plan_view(self, plan_id: str) -> VoicePlanView:
        plan = self.session.get(VoicePlan, plan_id)
        if plan is None:
            raise ValueError("VOICE_PLAN_NOT_FOUND")
        return VoicePlanView(
            id=plan.id,
            chapter_id=plan.chapter_id,
            mode=VoiceMode(plan.mode),
            narrator_preset_id=plan.narrator_preset_id,
            plan_sha256=plan.plan_sha256,
            roles=tuple(
                VoiceRoleView(
                    id=role.id,
                    role_key=role.role_key,
                    display_name=role.display_name,
                    voice_preset_id=role.voice_preset_id,
                    is_narrator=role.is_narrator,
                )
                for role in self._roles(plan.id)
            ),
            segments=tuple(
                SpeechSegmentView(
                    id=segment.id,
                    translation_segment_id=segment.translation_segment_id,
                    narration_text=segment.narration_text,
                    narration_sha256=segment.narration_sha256,
                    narration_diff=derive_narration(
                        self.session.get(
                            TranslationSegment,
                            segment.translation_segment_id,
                        ).target_text
                    ).diff,
                    estimated_duration_ms=segment.estimated_duration_ms or 0,
                    synthesis_cache_key=segment.synthesis_cache_key,
                )
                for segment in self._speech_segments(plan.id)
            ),
        )

    def _chapter(self, chapter_id: str) -> Chapter:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError("CHAPTER_NOT_FOUND")
        return chapter

    def _approved_run(self, chapter: Chapter) -> TranslationRun:
        if chapter.approved_translation_run_id is None:
            raise TranslationApprovalRequired("TRANSLATION_APPROVAL_REQUIRED")
        run = self.session.get(TranslationRun, chapter.approved_translation_run_id)
        if run is None or run.status != RunStatus.APPROVED.value:
            raise TranslationApprovalRequired("TRANSLATION_APPROVAL_REQUIRED")
        return run

    def _voice_preset(self, preset_id: str) -> VoicePreset:
        preset = self.session.get(VoicePreset, preset_id)
        if preset is None:
            raise ValueError("VOICE_PRESET_NOT_FOUND")
        return preset

    def _next_plan_revision(self, chapter_id: str) -> int:
        current = self.session.scalar(
            select(func.max(VoicePlan.revision_no)).where(VoicePlan.chapter_id == chapter_id)
        )
        return int(current or 0) + 1

    def _translation_segments(self, run_id: str) -> tuple[TranslationSegment, ...]:
        return tuple(
            self.session.scalars(
                select(TranslationSegment)
                .where(TranslationSegment.translation_run_id == run_id)
                .order_by(TranslationSegment.created_at, TranslationSegment.id)
            )
        )

    def _roles(self, plan_id: str) -> tuple[VoiceRole, ...]:
        return tuple(
            self.session.scalars(
                select(VoiceRole)
                .where(VoiceRole.voice_plan_id == plan_id)
                .order_by(VoiceRole.is_narrator.desc(), VoiceRole.id)
            )
        )

    def _speech_segments(self, plan_id: str) -> tuple[SpeechSegment, ...]:
        return tuple(
            self.session.scalars(
                select(SpeechSegment)
                .where(SpeechSegment.voice_plan_id == plan_id)
                .order_by(SpeechSegment.segment_index, SpeechSegment.id)
            )
        )

    def _artifact_path(self, relative_path: str) -> Path:
        root = self.artifact_root.resolve()
        path = (root / relative_path).resolve()
        if not path.is_relative_to(root):
            raise ValueError("UNSAFE_ARTIFACT_PATH")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


@dataclass(frozen=True)
class _MasterWrite:
    artifact: Artifact
    result: MasterResult


def _srt_text(
    segments: tuple[SpeechSegment, ...],
    durations: tuple[int, ...],
    master_duration_ms: int,
) -> str:
    lines: list[str] = []
    current_ms = 0
    for index, (segment, duration_ms) in enumerate(zip(segments, durations, strict=True), start=1):
        start_ms = current_ms
        end_ms = min(master_duration_ms, current_ms + duration_ms)
        lines.extend(
            (
                str(index),
                f"{_srt_timestamp(start_ms)} --> {_srt_timestamp(end_ms)}",
                segment.narration_text,
                "",
            )
        )
        current_ms = min(master_duration_ms, end_ms + segment.pause_after_ms)
    return "\n".join(lines)


def _srt_timestamp(milliseconds: int) -> str:
    seconds, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{sec:02d},{ms:03d}"


def _sha256_file(path: Path) -> str:
    sha256 = hashlib.sha256()
    with Path(path).open("rb") as file:
        while chunk := file.read(1024 * 1024):
            sha256.update(chunk)
    return sha256.hexdigest()


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
