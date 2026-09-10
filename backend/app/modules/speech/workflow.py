from __future__ import annotations

from collections.abc import Callable, Mapping
import asyncio
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Protocol

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
    SynthesisResult,
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
from app.modules.artifacts.cache import ArtifactCache
from app.modules.artifacts.store import ArtifactStore, ArtifactWrite
from app.modules.audio.qa import AudioIssueDraft, run_master_qa, run_premaster_qa
from app.modules.projects.state_machine import next_state
from app.modules.speech.narration import (
    derive_narration,
    estimate_duration_ms,
    narration_chunks,
)
from app.providers.ffmpeg_audio import FFmpegAudioProcessor
from app.providers.piper import PiperTtsAdapter
from app.providers.vieneu import VieNeuTtsAdapter


ZERO_HASH = "0" * 64
SPEECH_SETTINGS_VERSION = "speech-single-v1"
PART_MAX_SEGMENTS = 50
PART_MAX_DURATION_MS = 600_000


class TranslationApprovalRequired(Exception):
    """Raised when speech work is requested before translation approval."""


class VoicePlanRequired(Exception):
    """Raised when rendering is requested before configuring a voice plan."""


class AudioApprovalBlocked(Exception):
    """Raised when major or critical audio QA issues are still open."""


class AudioApprovalConflict(Exception):
    """Raised when the requested master artifact is stale or invalid."""


class TtsUnavailable(ValueError):
    """Raised when no verified local TTS adapter is available for publication render."""


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
    role_id: str | None = None
    role_key: str | None = None


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
    part_artifact_ids: tuple[str, ...]
    srt_artifact_id: str


@dataclass(frozen=True)
class SynthesizedChapterView:
    """Result of a resumable segment render, before any master is produced."""

    chapter_id: str
    segment_ids: tuple[str, ...]
    reused_segment_ids: tuple[str, ...]
    rendered_segment_ids: tuple[str, ...]
    tts_artifact_ids: tuple[str, ...]
    state: str


class RenderCheckpoint(Protocol):
    """Durable per-segment checkpoint hook used by the worker-driven render (A03).

    RecoveryJobContext satisfies this structurally: commit publishes the work finished
    for one segment, and raise_if_cancel_requested stops the pass at a segment boundary
    by raising RecoveryCanceled for the worker to acknowledge.
    """

    def raise_if_cancel_requested(self) -> None: ...

    def commit(self) -> None: ...


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
        allow_fake_tts: bool = False,
    ) -> None:
        self.session = session
        self.tts = tts
        self.audio_processor = audio_processor or FFmpegAudioProcessor()
        self.artifact_root = Path(artifact_root or Path("data") / "artifacts")
        self.id_factory = id_factory
        self.allow_fake_tts = allow_fake_tts
        self._render_cloud_consent_id: str | None = None
        self._render_budget_authorization_id: str | None = None

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

    def enqueue_render(
        self,
        chapter_id: str,
        *,
        cloud_consent_id: str | None = None,
        budget_authorization_id: str | None = None,
    ) -> RenderedChapterView:
        self._render_cloud_consent_id = cloud_consent_id
        self._render_budget_authorization_id = budget_authorization_id
        try:
            return self._render(chapter_id, force_segment_ids=frozenset())
        finally:
            self._render_cloud_consent_id = None
            self._render_budget_authorization_id = None

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
        metadata = artifact.metadata_json or {}
        if metadata.get("stage") == "part":
            # A part is an intermediate artifact: only the final concat master
            # (which references the parts in its manifest metadata) is
            # approvable, so review always covers the whole chapter.
            raise AudioApprovalConflict("PART_ARTIFACT_NOT_APPROVABLE")
        if artifact.sha256 != expected_sha256:
            raise AudioApprovalConflict("MASTER_HASH_MISMATCH")
        if metadata.get("translation_run_id") != chapter.approved_translation_run_id:
            raise AudioApprovalConflict("MASTER_TRANSLATION_STALE")
        if metadata.get("voice_plan_id") != chapter.active_voice_plan_id:
            raise AudioApprovalConflict("MASTER_VOICE_PLAN_STALE")
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

    def synthesize_segments(
        self,
        chapter_id: str,
        *,
        checkpoint: RenderCheckpoint | None = None,
        force_segment_ids: frozenset[str] = frozenset(),
    ) -> SynthesizedChapterView:
        """Render TTS_SEGMENT artifacts resumably, one durable checkpoint per segment.

        Every segment is synthesized into a staging file, probe-verified against the
        digest the adapter reported and stored through ArtifactStore (atomic os.link
        inside the artifact root) before its artifact row is marked READY. When a
        checkpoint is supplied (the worker path) the session is committed after each
        segment, so a crash, an expired lease or a cancel request never discards the
        segments that already finished: the next attempt serves them from the synthesis
        cache instead of paying for them again.

        Cancellation is observed at segment boundaries, so the segment in flight always
        finishes and is checkpointed. A cache miss flushes and closes the write
        transaction before the adapter is called, so provider I/O never runs inside an
        open SQLite transaction. This method deliberately stops at segment audio; the
        master/SRT stage stays with the inline render (A04 masters parts there).
        """
        chapter = self._chapter(chapter_id)
        context = self._render_context(chapter)
        writes = self._render_segment_artifacts(
            context,
            force_segment_ids=force_segment_ids,
            checkpoint=checkpoint,
        )
        self._commit(checkpoint)
        return SynthesizedChapterView(
            chapter_id=chapter.id,
            segment_ids=tuple(segment.id for segment in context.segments),
            reused_segment_ids=writes.reused_segment_ids,
            rendered_segment_ids=writes.rendered_segment_ids,
            tts_artifact_ids=writes.artifact_ids,
            state=chapter.state,
        )

    def _render(
        self,
        chapter_id: str,
        *,
        force_segment_ids: frozenset[str],
    ) -> RenderedChapterView:
        chapter = self._chapter(chapter_id)
        context = self._render_context(chapter)
        writes = self._render_segment_artifacts(
            context,
            force_segment_ids=force_segment_ids,
            checkpoint=None,
        )
        master = self._master(
            chapter,
            context.run,
            context.plan,
            writes.audio_paths,
            context.segments,
            writes.artifact_ids,
            # An explicit regenerate must hand back a NEW master (the caller
            # asked for it and downstream approvals key on master_sha256);
            # only an untouched re-render may reuse the cached final.
            force_final=bool(force_segment_ids),
        )
        srt = self._write_srt(
            chapter,
            master.result,
            context.segments,
            master.parts,
            writes.durations,
        )
        chapter.state = ChapterState.AUDIO_REVIEW.value
        self.session.commit()
        return RenderedChapterView(
            chapter_id=chapter.id,
            segment_ids=tuple(segment.id for segment in context.segments),
            reused_segment_ids=writes.reused_segment_ids,
            rendered_segment_ids=writes.rendered_segment_ids,
            tts_artifact_ids=writes.artifact_ids,
            master_artifact_id=master.artifact.id,
            master_sha256=master.artifact.sha256,
            part_artifact_ids=tuple(part.artifact.id for part in master.parts),
            srt_artifact_id=srt.id,
        )

    def _render_context(self, chapter: Chapter) -> _RenderContext:
        """Resolve the active plan, the approved run and the per-role presets to render."""
        if chapter.active_voice_plan_id is None:
            raise VoicePlanRequired("VOICE_PLAN_REQUIRED")
        plan = self.session.get(VoicePlan, chapter.active_voice_plan_id)
        if plan is None:
            raise VoicePlanRequired("VOICE_PLAN_REQUIRED")
        run = self._approved_run(chapter)
        self._require_current_voice_plan(plan, run)
        segments = self._speech_segments(plan.id)
        if not segments:
            raise VoicePlanRequired("SPEECH_SEGMENTS_REQUIRED")
        roles_by_id = {role.id: role for role in self._roles(plan.id)}
        preset_by_role_id = {
            role.id: self._voice_preset(role.voice_preset_id)
            for role in roles_by_id.values()
        }
        return _RenderContext(
            chapter=chapter,
            plan=plan,
            run=run,
            segments=segments,
            preset_by_role_id=preset_by_role_id,
        )

    def _render_segment_artifacts(
        self,
        context: _RenderContext,
        *,
        force_segment_ids: frozenset[str],
        checkpoint: RenderCheckpoint | None,
    ) -> _SegmentWrites:
        """Render every speech segment, checkpointing each finished segment.

        Cache hits are reused, cache misses are synthesized and stored atomically and,
        once the pass ends, READY segment audio whose fingerprint is no longer the one
        this plan computes is retired (SUPERSEDED), so a narration text or voice change
        leaves no live stale artifact behind.
        """
        chapter = context.chapter
        self._advance_to_synthesizing(chapter)
        store = ArtifactStore(self.artifact_root)
        rendered_segment_ids: list[str] = []
        reused_segment_ids: list[str] = []
        artifact_ids: list[str] = []
        audio_paths: list[Path] = []
        durations: list[int] = []
        for segment in context.segments:
            if checkpoint is not None:
                # Safe cancellation point: only whole, checkpointed segments exist.
                checkpoint.raise_if_cancel_requested()
            if segment.role_id not in context.preset_by_role_id:
                raise VoicePlanRequired("VOICE_ROLE_NOT_FOUND")
            preset = context.preset_by_role_id[segment.role_id]
            tts = self._tts_for_preset(preset)
            settings_hash = self._settings_hash(preset)
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
                durations.append(
                    cached.duration_ms or segment.estimated_duration_ms or 0
                )
                continue

            if checkpoint is not None:
                # Flush the cache key and close the transaction before provider I/O.
                checkpoint.commit()
            self._supersede_ready_artifacts(
                ArtifactKind.TTS_SEGMENT,
                cache_key,
                settings_hash,
            )
            artifact_id = self.id_factory()
            relative_path = f"audio/{chapter.id}/segments/{artifact_id}.wav"
            output_path = self._artifact_path(relative_path)
            result, payload = self._synthesize_segment_payload(
                tts, segment, preset, cache_key, output_path, artifact_id
            )
            artifact = self._store_segment_artifact(
                store,
                artifact_id=artifact_id,
                chapter_id=chapter.id,
                relative_path=relative_path,
                payload=payload,
                duration_ms=result.duration_ms,
                input_hash=cache_key,
                settings_hash=settings_hash,
                speech_segment_id=segment.id,
            )
            rendered_segment_ids.append(segment.id)
            artifact_ids.append(artifact.id)
            audio_paths.append(output_path)
            durations.append(result.duration_ms)
            if checkpoint is not None:
                # Durable checkpoint: the READY artifact row and the segment cache key
                # are committed before the next segment starts.
                checkpoint.commit()
        self._retire_stale_segment_artifacts(
            chapter, used_artifact_ids=frozenset(artifact_ids)
        )
        return _SegmentWrites(
            rendered_segment_ids=tuple(rendered_segment_ids),
            reused_segment_ids=tuple(reused_segment_ids),
            artifact_ids=tuple(artifact_ids),
            audio_paths=tuple(audio_paths),
            durations=tuple(durations),
        )

    def _advance_to_synthesizing(self, chapter: Chapter) -> None:
        if chapter.state == ChapterState.VOICE_CONFIGURED.value:
            chapter.state = next_state(chapter.state, ChapterState.TTS_QUEUED).value
        if chapter.state == ChapterState.TTS_QUEUED.value:
            chapter.state = next_state(chapter.state, ChapterState.SYNTHESIZING).value

    def _synthesize_segment_payload(
        self,
        tts: TtsAdapter,
        segment: SpeechSegment,
        preset: VoicePreset,
        cache_key: str,
        output_path: Path,
        artifact_id: str,
    ) -> tuple[SynthesisResult, bytes]:
        """Run the adapter into a staging file and probe the bytes it wrote.

        The adapter never writes the final artifact path: the bytes are verified against
        the digest the adapter reported and only then handed to ArtifactStore, so a
        failed or truncated synthesis can never leave a READY or half written artifact.
        """
        staging_path = output_path.with_name(f".tmp-{artifact_id}.wav")
        try:
            result = asyncio.run(
                tts.synthesize(
                    self._synthesis_request(segment, preset, cache_key), staging_path
                )
            )
            actual_sha256 = _sha256_file(staging_path)
            if actual_sha256 != result.sha256:
                raise ValueError("TTS_CHECKSUM_MISMATCH")
            return result, staging_path.read_bytes()
        finally:
            staging_path.unlink(missing_ok=True)

    def _store_segment_artifact(
        self,
        store: ArtifactStore,
        *,
        artifact_id: str,
        chapter_id: str,
        relative_path: str,
        payload: bytes,
        duration_ms: int,
        input_hash: str,
        settings_hash: str,
        speech_segment_id: str,
    ) -> Artifact:
        """Write the verified bytes atomically, then publish the READY artifact row."""
        write = ArtifactWrite(
            kind=ArtifactKind.TTS_SEGMENT,
            relative_path=relative_path,
            input_hash=input_hash,
            settings_hash=settings_hash,
            mime_type="audio/wav",
        )
        with store.begin(write) as writer:
            writer.file.write(payload)
            stored = writer.commit()
        return self._add_artifact(
            artifact_id=artifact_id,
            chapter_id=chapter_id,
            kind=ArtifactKind.TTS_SEGMENT,
            relative_path=stored.relative_path,
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            mime_type="audio/wav",
            duration_ms=duration_ms,
            input_hash=input_hash,
            settings_hash=settings_hash,
            metadata={"speech_segment_id": speech_segment_id},
        )

    def _retire_stale_segment_artifacts(
        self,
        chapter: Chapter,
        *,
        used_artifact_ids: frozenset[str],
    ) -> None:
        """Supersede READY segment audio that the finished pass no longer serves.

        A whole pass touches every segment of the active plan, so any READY segment
        artifact of this chapter that the pass neither rendered nor reused (edited
        narration text, changed role preset, superseded plan revision) is retired while
        reused audio keeps serving. Nothing is deleted: only the READY flag moves.
        """
        for artifact in self.session.scalars(
            select(Artifact).where(
                Artifact.chapter_id == chapter.id,
                Artifact.kind == ArtifactKind.TTS_SEGMENT.value,
                Artifact.status == ArtifactStatus.READY.value,
            )
        ):
            if artifact.id not in used_artifact_ids:
                artifact.status = ArtifactStatus.SUPERSEDED.value
        self.session.flush()

    def _commit(self, checkpoint: RenderCheckpoint | None) -> None:
        if checkpoint is None:
            self.session.commit()
        else:
            checkpoint.commit()

    def _master(
        self,
        chapter: Chapter,
        run: TranslationRun,
        plan: VoicePlan,
        audio_paths: tuple[Path, ...],
        segments: tuple[SpeechSegment, ...],
        tts_artifact_ids: tuple[str, ...] = (),
        force_final: bool = False,
    ) -> _MasterWrite:
        """Master the chapter through bounded parts, then concat the final master.

        Each part covers at most PART_MAX_SEGMENTS segments or
        PART_MAX_DURATION_MS of estimated speech (pauses excluded) and is
        mastered as its own MASTER_MP3 artifact with an input_hash over exactly
        its own segments and pauses, so a single edited segment rebuilds only
        the part containing it while the untouched parts and the final concat
        reuse their cached artifacts. Every part is probe-verified before its
        artifact row is written; a failed probe raises before anything is
        marked READY, leaving the previously READY parts untouched, and a
        failed processor call destroys no segment artifact at all.
        """
        premaster_issues = run_premaster_qa(
            audio_paths,
            tuple(segment.id for segment in segments),
            pause_after_ms=tuple(segment.pause_after_ms for segment in segments),
        )
        master_settings = _master_settings_hash()
        groups = _part_groups(segments)
        parts: list[_PartWrite] = []
        group_paths = _group_paths(audio_paths, groups)
        group_artifact_ids = _group_paths(tts_artifact_ids or ("",) * len(segments), groups)
        for part_index, (group, paths, artifact_ids) in enumerate(
            zip(groups, group_paths, group_artifact_ids, strict=True)
        ):
            parts.append(
                self._render_part(
                    chapter,
                    run,
                    plan,
                    part_index=part_index,
                    part_count=len(groups),
                    segments=group,
                    audio_paths=paths,
                    tts_artifact_ids=artifact_ids,
                    settings_hash=master_settings,
                )
            )
        final = self._concat_final_master(
            chapter,
            run,
            plan,
            parts,
            total_segments=len(segments),
            settings_hash=master_settings,
            force_final=force_final,
        )
        self._replace_audio_qa(chapter.id, final.result, premaster_issues)
        return _MasterWrite(
            artifact=final.artifact,
            result=final.result,
            parts=tuple(parts),
        )

    def _render_part(
        self,
        chapter: Chapter,
        run: TranslationRun,
        plan: VoicePlan,
        *,
        part_index: int,
        part_count: int,
        segments: tuple[SpeechSegment, ...],
        audio_paths: tuple[Path, ...],
        tts_artifact_ids: tuple[str, ...] = (),
        settings_hash: str,
    ) -> _PartWrite:
        """Render one part, or reuse its cached artifact when nothing changed."""
        input_hash = _part_input_hash(
            run, plan, segments, audio_paths, tts_artifact_ids
        )
        cached = self._ready_artifact(
            ArtifactKind.MASTER_MP3, input_hash, settings_hash
        )
        if cached is not None:
            metadata = cached.metadata_json or {}
            return _PartWrite(
                artifact=cached,
                input_hash=input_hash,
                duration_ms=int(cached.duration_ms or 0),
                part_index=part_index,
                segment_count=len(segments),
                rendered=False,
                pause_after_last_ms=int(metadata.get("pause_after_last_ms") or 0),
            )
        result = self._run_master_request(
            chapter=chapter,
            run=run,
            plan=plan,
            artifact_id=self.id_factory(),
            kind_name="part",
            part_index=part_index,
            part_count=part_count,
            segments=segments,
            audio_paths=audio_paths,
            extra_metadata={"stage": "part"},
            input_hash=input_hash,
            settings_hash=settings_hash,
        )
        artifact = result.artifact
        return _PartWrite(
            artifact=artifact,
            input_hash=input_hash,
            duration_ms=result.result.duration_ms,
            part_index=part_index,
            segment_count=len(segments),
            rendered=True,
            pause_after_last_ms=segments[-1].pause_after_ms,
        )

    def _concat_final_master(
        self,
        chapter: Chapter,
        run: TranslationRun,
        plan: VoicePlan,
        parts: list[_PartWrite],
        *,
        total_segments: int,
        settings_hash: str,
        force_final: bool = False,
    ) -> _MasterWrite:
        """Concat the part files into the final master, reusing the cached one.

        Reuse is only allowed for an untouched re-render (``force_final`` false):
        an explicit regeneration always produces a new final master so the
        caller-visible id/sha changes and downstream approvals go stale.
        """
        if not parts:
            raise VoicePlanRequired("SPEECH_SEGMENTS_REQUIRED")
        ordered_paths = tuple(part.artifact.relative_path for part in parts)
        ordered_hashes = tuple(part.artifact.sha256 for part in parts)
        ordered_input_hashes = tuple(part.input_hash for part in parts)
        part_durations = tuple(part.duration_ms for part in parts)
        pause_after_parts = tuple(part.pause_after_last_ms for part in parts)
        total_duration_ms = sum(part_durations) + sum(pause_after_parts)
        input_hash = _canonical_sha256(
            {
                "translation_run": run.id,
                "plan": plan.plan_sha256,
                "stage": "final-master",
                "part_count": len(parts),
                "total_segments": total_segments,
                "parts": ordered_hashes,
                "part_inputs": ordered_input_hashes,
            }
        )
        cached = None if force_final else self._ready_artifact(
            ArtifactKind.MASTER_MP3, input_hash, settings_hash
        )
        if cached is not None:
            return _MasterWrite(
                artifact=cached,
                result=MasterResult(
                    duration_ms=int(cached.duration_ms or total_duration_ms),
                    sha256=cached.sha256,
                    codec="mp3",
                    sample_rate=44_100,
                    channels=1,
                    bitrate_kbps=128,
                    integrated_lufs=-16.0,
                    true_peak_dbtp=-1.5,
                ),
                parts=tuple(parts),
            )
        result = self._run_master_request(
            chapter=chapter,
            run=run,
            plan=plan,
            artifact_id=self.id_factory(),
            kind_name="master",
            part_index=len(parts) - 1,
            part_count=len(parts),
            segments=(),
            audio_paths=(),
            extra_metadata={
                "stage": "final",
                "part_paths": list(ordered_paths),
                "part_sha256s": list(ordered_hashes),
                "part_input_hashes": list(ordered_input_hashes),
                "part_durations_ms": list(part_durations),
                "pause_after_parts_ms": list(pause_after_parts),
                "total_segments": total_segments,
            },
            input_hash=input_hash,
            settings_hash=settings_hash,
            final_parts=parts,
        )
        # Only now that the new final master exists may the previous master and
        # the non-reused parts leave READY: a processor failure above leaves the
        # old master untouched (still READY, file intact).
        self._supersede_ready_masters_excluding(
            chapter.id, parts, keep_id=result.artifact.id
        )
        return _MasterWrite(
            artifact=result.artifact,
            result=result.result,
            parts=tuple(parts),
        )

    def _supersede_ready_masters_excluding(
        self,
        chapter_id: str,
        parts: list[_PartWrite],
        *,
        keep_id: str,
    ) -> None:
        """Move READY masters and parts aside without deleting any file.

        Called after a new final master exists: the previous final master and
        any part artifact the new render does not reuse become SUPERSEDED (the
        old master file stays on disk for review/rollback), while the new final
        and the reused parts keep serving.
        """
        keep_ids = {part.artifact.id for part in parts} | {keep_id}
        for artifact in self.session.scalars(
            select(Artifact).where(
                Artifact.chapter_id == chapter_id,
                Artifact.kind == ArtifactKind.MASTER_MP3.value,
                Artifact.status == ArtifactStatus.READY.value,
            )
        ):
            if artifact.id not in keep_ids:
                artifact.status = ArtifactStatus.SUPERSEDED.value
        self.session.flush()

    def _supersede_ready_srts(
        self, chapter_id: str, *, keep_id: str | None = None
    ) -> None:
        for artifact in self.session.scalars(
            select(Artifact).where(
                Artifact.chapter_id == chapter_id,
                Artifact.kind == ArtifactKind.SRT.value,
                Artifact.status == ArtifactStatus.READY.value,
            )
        ):
            if artifact.id == keep_id:
                continue
            artifact.status = ArtifactStatus.SUPERSEDED.value
        self.session.flush()

    def _run_master_request(
        self,
        *,
        chapter: Chapter,
        run: TranslationRun,
        plan: VoicePlan,
        artifact_id: str,
        kind_name: str,
        part_index: int,
        part_count: int,
        segments: tuple[SpeechSegment, ...],
        audio_paths: tuple[Path, ...],
        extra_metadata: dict[str, object],
        input_hash: str,
        settings_hash: str,
        final_parts: list[_PartWrite] | None = None,
    ) -> _MasterWrite:
        """Run the processor once, verify the digest, then publish READY.

        For a part the request carries the segment WAVs and the per-segment
        pauses (the closing pause of the part is included here so it is audible
        in the part too, and it is replayed between parts by the final concat
        without being doubled). For the final master the request carries the
        ordered part files with one pause after each part.
        """
        if final_parts is None:
            ordered_paths = audio_paths
            pauses = tuple(segment.pause_after_ms for segment in segments)
        else:
            ordered_paths = tuple(
                self._artifact_path(part.artifact.relative_path)
                for part in final_parts
            )
            pauses = tuple(part.pause_after_last_ms for part in final_parts)
        metadata: dict[str, str] = {
            "title": chapter.translated_title
            or chapter.source_title
            or "Chapter",
            "album": chapter.project_id,
        }
        part_metadata = {
            "part_index": part_index,
            "part_count": part_count,
            "stage": extra_metadata.get("stage", kind_name),
            "translation_run_id": run.id,
            "voice_plan_id": plan.id,
            "voice_plan_sha256": plan.plan_sha256,
        }
        output_id = artifact_id
        relative_path = f"audio/{chapter.id}/masters/{output_id}.mp3"
        output_path = self._artifact_path(relative_path)
        result = asyncio.run(
            self.audio_processor.master(
                MasterRequest(
                    operation_id=f"{kind_name}:{chapter.id}:{output_id}",
                    ordered_segment_paths=ordered_paths,
                    pause_after_ms=pauses,
                    metadata=metadata,
                    sample_rate=44_100,
                ),
                output_path,
            )
        )
        actual_sha256 = _sha256_file(output_path)
        if actual_sha256 != result.sha256:
            output_path.unlink(missing_ok=True)
            raise ValueError("MASTER_CHECKSUM_MISMATCH")
        probe = asyncio.run(
            self.audio_processor.probe(output_path, expected_sha256=result.sha256)
        )
        if probe.duration_ms <= 0:
            raise ValueError("MASTER_PROBE_DURATION_INVALID")
        if probe.sample_rate != 44_100 or probe.channels != 1:
            raise ValueError("MASTER_PROBE_FORMAT_INVALID")
        merged_metadata: dict[str, object] = {
            "codec": result.codec,
            "sample_rate": result.sample_rate,
            "channels": result.channels,
            "bitrate_kbps": result.bitrate_kbps,
            "integrated_lufs": result.integrated_lufs,
            "true_peak_dbtp": result.true_peak_dbtp,
            **part_metadata,
            **extra_metadata,
        }
        if final_parts is None:
            merged_metadata["speech_segment_ids"] = [
                segment.id for segment in segments
            ]
            merged_metadata["pause_after_last_ms"] = (
                segments[-1].pause_after_ms if segments else 0
            )
        artifact = self._add_artifact(
            artifact_id=output_id,
            chapter_id=chapter.id,
            kind=ArtifactKind.MASTER_MP3,
            relative_path=relative_path,
            sha256=result.sha256,
            byte_size=output_path.stat().st_size,
            mime_type="audio/mpeg",
            duration_ms=result.duration_ms,
            input_hash=input_hash,
            settings_hash=settings_hash,
            metadata=merged_metadata,
        )
        return _MasterWrite(artifact=artifact, result=result)

    def _write_srt(
        self,
        chapter: Chapter,
        master: MasterResult,
        segments: tuple[SpeechSegment, ...],
        parts: tuple[_PartWrite, ...],
        durations: tuple[int, ...],
    ) -> Artifact:
        input_hash = _canonical_sha256(
            {
                "master": master.sha256,
                "segments": [segment.narration_sha256 for segment in segments],
                "part_boundaries": [
                    part.segment_count for part in parts
                ],
                "version": "srt-v2-parts",
            }
        )
        settings_hash = _canonical_sha256(
            {"format": "srt", "max_end_ms": master.duration_ms}
        )
        cached = self._ready_artifact(ArtifactKind.SRT, input_hash, settings_hash)
        if cached is not None:
            self._supersede_ready_srts(chapter.id, keep_id=cached.id)
            return cached
        artifact_id = self.id_factory()
        relative_path = f"audio/{chapter.id}/subtitles/{artifact_id}.srt"
        output_path = self._artifact_path(relative_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            _srt_text(segments, durations, master.duration_ms, parts),
            encoding="utf-8",
        )
        artifact = self._add_artifact(
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
        self._supersede_ready_srts(chapter.id, keep_id=artifact.id)
        return artifact

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
                budget_authorization_id=self._render_budget_authorization_id,
                cloud_consent_id=self._render_cloud_consent_id,
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
        return ArtifactCache(self.session, self.artifact_root).lookup(
            kind, input_hash, settings_hash
        )

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
        """Fingerprint one segment's audio.

        The narration text itself is part of the key (not only its stored digest), so an
        edited narration text invalidates exactly the segments it touched even when the
        row was written without refreshing narration_sha256, while unchanged segments
        keep their fingerprint and stay cache hits.
        """
        capabilities = self._tts_for_preset(preset).capabilities()
        return _canonical_sha256(
            {
                "narration": segment.narration_text,
                "narration_sha256": segment.narration_sha256,
                "provider": str(capabilities.get("provider") or "unknown"),
                "model": str(capabilities.get("model") or "unknown"),
                "provider_version": str(capabilities.get("provider_version") or "unknown"),
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
        plan = self.session.get(VoicePlan, plan_id)
        if plan is None:
            raise ValueError("VOICE_PLAN_NOT_FOUND")
        segments = self._speech_segments(plan_id)
        return _canonical_sha256(
            {
                "mode": plan.mode,
                "translation_run_id": run.id,
                "translation_hash": run.translation_text_sha256,
                "roles": [
                    {
                        "role_key": role.role_key,
                        "voice_preset_id": role.voice_preset_id,
                        "is_narrator": role.is_narrator,
                    }
                    for role in self._roles(plan_id)
                ],
                "segments": [
                    {
                        "segment_index": segment.segment_index,
                        "translation_segment_id": segment.translation_segment_id,
                        "narration_sha256": segment.narration_sha256,
                        "pronunciation_hash": segment.pronunciation_revision_hash,
                        "role_id": segment.role_id,
                    }
                    for segment in segments
                ],
            }
        )

    def _plan_view(self, plan_id: str) -> VoicePlanView:
        plan = self.session.get(VoicePlan, plan_id)
        if plan is None:
            raise ValueError("VOICE_PLAN_NOT_FOUND")
        roles = self._roles(plan.id)
        role_by_id = {role.id: role for role in roles}
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
                for role in roles
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
                    role_id=segment.role_id,
                    role_key=role_by_id[segment.role_id].role_key
                    if segment.role_id in role_by_id
                    else None,
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

    def _require_current_voice_plan(self, plan: VoicePlan, run: TranslationRun) -> None:
        segment_run_ids = {
            segment.translation_run_id for segment in self._speech_segments(plan.id)
        }
        if segment_run_ids != {run.id}:
            raise VoicePlanRequired("VOICE_PLAN_STALE_TRANSLATION")

    def _tts_for_preset(self, preset: VoicePreset) -> TtsAdapter:
        if self.tts is not None:
            self._require_verified_voice_preset(preset)
            return self.tts
        self._require_verified_voice_preset(preset)
        settings = preset.settings_json or {}
        provider = str(settings.get("provider") or "").lower()
        if provider == "piper":
            return PiperTtsAdapter(
                binary_path=self._settings_path(settings, "binary_path"),
                model_path=self._settings_path(settings, "model_path"),
                config_path=self._settings_path(settings, "config_path"),
                model_sha256=preset.model_snapshot_hash or "",
            )
        if provider == "vieneu":
            return VieNeuTtsAdapter(
                binary_path=self._settings_path(settings, "binary_path"),
                model_path=self._settings_path(settings, "model_path"),
                model_sha256=preset.model_snapshot_hash or "",
            )
        raise TtsUnavailable("LOCAL_TTS_ADAPTER_REQUIRED")

    def _require_verified_voice_preset(self, preset: VoicePreset) -> None:
        if self.allow_fake_tts:
            return
        if (
            not preset.model_snapshot_hash
            or preset.license_snapshot_artifact_id is None
        ):
            raise TtsUnavailable("VOICE_MODEL_LICENSE_UNVERIFIED")
        license_artifact = self.session.get(
            Artifact, preset.license_snapshot_artifact_id
        )
        if (
            license_artifact is None
            or license_artifact.status != ArtifactStatus.READY.value
        ):
            raise TtsUnavailable("VOICE_MODEL_LICENSE_UNVERIFIED")
        if (preset.settings_json or {}).get("engine") == "fake" or (
            preset.provider_voice_id or ""
        ).startswith("fake"):
            raise TtsUnavailable("FAKE_TTS_NOT_ALLOWED")

    def _settings_path(self, settings: dict[str, object], key: str) -> Path:
        value = settings.get(key)
        if not isinstance(value, str) or not value.strip():
            raise TtsUnavailable("LOCAL_TTS_ADAPTER_REQUIRED")
        path = Path(value)
        if not path.is_absolute():
            path = self.artifact_root.parent / path
        return path

    def _voice_preset(self, preset_id: str) -> VoicePreset:
        preset = self.session.get(VoicePreset, preset_id)
        if preset is None:
            raise ValueError("VOICE_PRESET_NOT_FOUND")
        return preset

    def _next_plan_revision(self, chapter_id: str) -> int:
        current = self.session.scalar(
            select(func.max(VoicePlan.revision_no)).where(
                VoicePlan.chapter_id == chapter_id
            )
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
class _RenderContext:
    """Everything one render pass needs, resolved before any provider call."""

    chapter: Chapter
    plan: VoicePlan
    run: TranslationRun
    segments: tuple[SpeechSegment, ...]
    preset_by_role_id: Mapping[str, VoicePreset]


@dataclass(frozen=True)
class _SegmentWrites:
    """Per-segment outcome of a render pass, in plan order."""

    rendered_segment_ids: tuple[str, ...]
    reused_segment_ids: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    audio_paths: tuple[Path, ...]
    durations: tuple[int, ...]


@dataclass(frozen=True)
class _MasterWrite:
    artifact: Artifact
    result: MasterResult
    parts: tuple[_PartWrite, ...] = ()


@dataclass(frozen=True)
class _PartWrite:
    """One mastered part and the numbers the final concat/SRT need."""

    artifact: Artifact
    input_hash: str
    duration_ms: int
    part_index: int
    segment_count: int
    rendered: bool
    pause_after_last_ms: int


def _srt_text(
    segments: tuple[SpeechSegment, ...],
    durations: tuple[int, ...],
    master_duration_ms: int,
    parts: tuple[_PartWrite, ...] = (),
) -> str:
    """Build the SRT timeline from real mastered durations.

    Without part durations the timeline walks per-segment estimates plus the
    pauses. With parts (A04) the segments are advanced part by part: the first
    segment of part N+1 starts at the sum of the real mastered durations of
    parts 1..N plus the inter-part pause after the last segment of part N, so
    a part boundary never skews the timestamps.
    """
    lines: list[str] = []
    if parts:
        boundary_starts = _part_boundary_starts(parts)
    else:
        boundary_starts = None
    current_ms = 0
    part_cursor = 0
    segments_into_part = 0
    for index, (segment, duration_ms) in enumerate(
        zip(segments, durations, strict=True), start=1
    ):
        if boundary_starts is not None:
            if part_cursor + 1 < len(parts):
                next_part_start = boundary_starts[part_cursor + 1]
                if segments_into_part >= parts[part_cursor].segment_count:
                    part_cursor += 1
                    segments_into_part = 0
                    current_ms = next_part_start
        start_ms = current_ms
        end_ms = current_ms + duration_ms
        if 0 < master_duration_ms < end_ms and master_duration_ms >= start_ms:
            # Keep the cues inside the real master length when the mastered
            # duration is credible for this cue, but never truncate the part
            # timeline itself.
            end_ms = master_duration_ms
        lines.extend(
            (
                str(index),
                f"{_srt_timestamp(start_ms)} --> {_srt_timestamp(end_ms)}",
                segment.narration_text,
                "",
            )
        )
        segments_into_part += 1
        current_ms = end_ms + segment.pause_after_ms
    return "\n".join(lines)


def _part_boundary_starts(parts: tuple[_PartWrite, ...]) -> tuple[int, ...]:
    starts: list[int] = [0]
    for part in parts[:-1]:
        starts.append(starts[-1] + part.duration_ms + part.pause_after_last_ms)
    return tuple(starts)


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


def _master_settings_hash() -> str:
    return _canonical_sha256(
        {
            "kind": "master-mp3",
            "sample_rate": 44_100,
            "channels": 1,
            "bitrate": "128k",
            "loudnorm": {"I": -16, "TP": -1.5, "LRA": 11},
            "id3v2": "2.3",
        }
    )


def _part_groups(
    segments: tuple[SpeechSegment, ...],
) -> tuple[tuple[SpeechSegment, ...], ...]:
    """Split ordered segments into parts of <=50 segments and <=10 minutes.

    The ten-minute budget counts estimated speech only (pause_after_ms is not
    speech and is inserted by the master request), and a part always ends on a
    segment boundary; the final part may be shorter than the limits.
    """
    groups: list[list[SpeechSegment]] = []
    current: list[SpeechSegment] = []
    current_ms = 0
    for segment in segments:
        segment_ms = segment.estimated_duration_ms or 0
        if current and (
            len(current) >= PART_MAX_SEGMENTS
            or current_ms + segment_ms > PART_MAX_DURATION_MS
        ):
            groups.append(current)
            current = []
            current_ms = 0
        current.append(segment)
        current_ms += segment_ms
    if current:
        groups.append(current)
    return tuple(tuple(group) for group in groups)


def _group_paths(
    items: tuple[Path | str, ...],
    groups: tuple[tuple[SpeechSegment, ...], ...],
) -> tuple[tuple[Path | str, ...], ...]:
    """Slice an ordered per-segment list into one slice per part."""
    slices: list[tuple[Path | str, ...]] = []
    cursor = 0
    for group in groups:
        size = len(group)
        slices.append(items[cursor : cursor + size])
        cursor += size
    if cursor != len(items):
        raise ValueError("PART_SEGMENT_PATHS_MISMATCH")
    return tuple(slices)


def _part_input_hash(
    run: TranslationRun,
    plan: VoicePlan,
    segments: tuple[SpeechSegment, ...],
    audio_paths: tuple[Path, ...],
    tts_artifact_ids: tuple[str, ...] = (),
) -> str:
    """Fingerprint one part over exactly its own inputs.

    Like a manifest: the ordered input artifact references plus their
    checksums and the pauses. Editing a segment changes only the part that
    contains it; every other part keeps its hash and is served from the
    artifact cache. A forced re-render that produced a fresh segment artifact
    also rebuilds its part even when the bytes are identical, because the
    manifest now references a different input artifact.
    """
    return _canonical_sha256(
        {
            "translation_run": run.id,
            "plan": plan.plan_sha256,
            "stage": "part",
            "first_segment_index": segments[0].segment_index,
            "last_segment_index": segments[-1].segment_index,
            "segments": [
                {
                    "id": segment.id,
                    "index": segment.segment_index,
                    "tts_artifact_id": artifact_id,
                    "narration_sha256": segment.narration_sha256,
                    "sha256": _sha256_file(path),
                }
                for segment, path, artifact_id in zip(
                    segments,
                    audio_paths,
                    tts_artifact_ids or ("",) * len(segments),
                    strict=True,
                )
            ],
            "pauses": [segment.pause_after_ms for segment in segments],
        }
    )


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
