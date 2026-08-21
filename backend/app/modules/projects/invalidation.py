from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import ArtifactKind, ArtifactStatus, ExportStatus, RunStatus
from app.db.base import utc_now
from app.db.models import (
    Artifact,
    Chapter,
    Export,
    Project,
    SourceRevision,
    SpeechSegment,
    TranslationRun,
    VoicePlan,
)


class ChangeKind(StrEnum):
    SOURCE_REVISION = "SOURCE_REVISION"
    PROJECT_METADATA = "PROJECT_METADATA"
    GLOSSARY = "GLOSSARY"
    STORY_MEMORY = "STORY_MEMORY"
    TARGET_TEXT = "TARGET_TEXT"
    PRONUNCIATION = "PRONUNCIATION"
    VOICE_ROLE = "VOICE_ROLE"
    MASTER_SETTINGS = "MASTER_SETTINGS"
    RIGHTS_EVIDENCE = "RIGHTS_EVIDENCE"


@dataclass(frozen=True)
class Change:
    kind: ChangeKind
    affected_ids: tuple[str, ...]

    @classmethod
    def source_revision(cls, *affected_ids: str) -> Change:
        return cls(ChangeKind.SOURCE_REVISION, tuple(affected_ids))

    @classmethod
    def project_metadata(cls, *affected_ids: str) -> Change:
        return cls(ChangeKind.PROJECT_METADATA, tuple(affected_ids))

    @classmethod
    def glossary(cls, *affected_ids: str) -> Change:
        return cls(ChangeKind.GLOSSARY, tuple(affected_ids))

    @classmethod
    def story_memory(cls, *affected_ids: str) -> Change:
        return cls(ChangeKind.STORY_MEMORY, tuple(affected_ids))

    @classmethod
    def target_text(cls, *affected_ids: str) -> Change:
        return cls(ChangeKind.TARGET_TEXT, tuple(affected_ids))

    @classmethod
    def pronunciation(cls, *affected_ids: str) -> Change:
        return cls(ChangeKind.PRONUNCIATION, tuple(affected_ids))

    @classmethod
    def voice_role(cls, *affected_ids: str) -> Change:
        return cls(ChangeKind.VOICE_ROLE, tuple(affected_ids))

    @classmethod
    def master_settings(cls, *affected_ids: str) -> Change:
        return cls(ChangeKind.MASTER_SETTINGS, tuple(affected_ids))

    @classmethod
    def rights_evidence(cls, *affected_ids: str) -> Change:
        return cls(ChangeKind.RIGHTS_EVIDENCE, tuple(affected_ids))


@dataclass(frozen=True)
class InvalidationPlan:
    reused: tuple[str, ...]
    invalidated: tuple[str, ...]
    revoked_exports: tuple[str, ...]


class InvalidationGraph:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._invalidated: set[str] = set()
        self._revoked_exports: set[str] = set()

    def plan(self, change: Change) -> InvalidationPlan:
        self._invalidated = set()
        self._revoked_exports = set()
        if change.kind is ChangeKind.SOURCE_REVISION:
            for chapter in self._chapters_for_source_revisions(change.affected_ids):
                self._invalidate_translation(chapter)
        elif change.kind in {ChangeKind.GLOSSARY, ChangeKind.STORY_MEMORY}:
            for chapter in self._chapters_for_projects_or_chapters(change.affected_ids):
                self._invalidate_translation(chapter)
        elif change.kind is ChangeKind.PROJECT_METADATA:
            for chapter in self._chapters_for_projects_or_chapters(change.affected_ids):
                self._invalidate_translation(chapter)
        elif change.kind is ChangeKind.TARGET_TEXT:
            self._invalidate_speech_segments(self._speech_for_translation_segments(change.affected_ids))
        elif change.kind is ChangeKind.PRONUNCIATION:
            self._invalidate_speech_segments(self._speech_for_speech_or_chapter(change.affected_ids))
        elif change.kind is ChangeKind.VOICE_ROLE:
            self._invalidate_speech_segments(self._speech_for_voice_roles(change.affected_ids))
        elif change.kind is ChangeKind.MASTER_SETTINGS:
            for chapter in self._chapters_for_projects_or_chapters(change.affected_ids):
                self._invalidate_master_chain(chapter)
        elif change.kind is ChangeKind.RIGHTS_EVIDENCE:
            for chapter in self._chapters_for_projects_or_chapters(change.affected_ids):
                self._revoke_exports(chapter)

        chapter_ids = self._changed_chapter_ids(change)
        reused = self._ready_reused_artifacts(chapter_ids)
        self.session.flush()
        return InvalidationPlan(
            reused=tuple(sorted(reused)),
            invalidated=tuple(sorted(self._invalidated)),
            revoked_exports=tuple(sorted(self._revoked_exports)),
        )

    def _invalidate_translation(self, chapter: Chapter) -> None:
        for run in self.session.scalars(
            select(TranslationRun).where(
                TranslationRun.chapter_id == chapter.id,
                TranslationRun.status.in_((RunStatus.APPROVED.value, RunStatus.REVIEW.value)),
            )
        ):
            run.status = RunStatus.SUPERSEDED.value
            self._invalidated.add(run.id)
        chapter.approved_translation_run_id = None
        chapter.translation_approved_at = None
        chapter.active_voice_plan_id = None
        self._invalidate_all_speech_artifacts(chapter.id)
        self._invalidate_master_chain(chapter)

    def _invalidate_speech_segments(self, segments: tuple[SpeechSegment, ...]) -> None:
        chapters: dict[str, Chapter] = {}
        for segment in segments:
            for artifact in self._tts_artifacts_for_speech(segment.id):
                self._supersede_artifact(artifact)
            chapter = chapters.get(segment.chapter_id)
            if chapter is None:
                loaded = self.session.get(Chapter, segment.chapter_id)
                if loaded is not None:
                    chapters[segment.chapter_id] = loaded
        for chapter in chapters.values():
            self._invalidate_master_chain(chapter)

    def _invalidate_all_speech_artifacts(self, chapter_id: str) -> None:
        speech_ids = tuple(
            self.session.scalars(select(SpeechSegment.id).where(SpeechSegment.chapter_id == chapter_id))
        )
        for speech_id in speech_ids:
            for artifact in self._tts_artifacts_for_speech(speech_id):
                self._supersede_artifact(artifact)

    def _invalidate_master_chain(self, chapter: Chapter) -> None:
        for artifact in self.session.scalars(
            select(Artifact).where(
                Artifact.chapter_id == chapter.id,
                Artifact.kind.in_((ArtifactKind.MASTER_MP3.value, ArtifactKind.SRT.value)),
                Artifact.status == ArtifactStatus.READY.value,
            )
        ):
            self._supersede_artifact(artifact)
        chapter.approved_master_artifact_id = None
        chapter.audio_approved_at = None
        self._revoke_exports(chapter)

    def _revoke_exports(self, chapter: Chapter) -> None:
        for export in self.session.scalars(
            select(Export).where(
                Export.chapter_id == chapter.id,
                Export.status == ExportStatus.READY.value,
            )
        ):
            export.status = ExportStatus.REVOKED.value
            export.revoked_at = utc_now()
            self._invalidated.add(export.id)
            self._revoked_exports.add(export.id)
            if export.bundle_artifact_id is not None:
                artifact = self.session.get(Artifact, export.bundle_artifact_id)
                if artifact is not None:
                    self._supersede_artifact(artifact)
        chapter.last_export_id = None

    def _supersede_artifact(self, artifact: Artifact) -> None:
        if artifact.status == ArtifactStatus.READY.value:
            artifact.status = ArtifactStatus.SUPERSEDED.value
        self._invalidated.add(artifact.id)

    def _tts_artifacts_for_speech(self, speech_segment_id: str) -> tuple[Artifact, ...]:
        return tuple(
            artifact
            for artifact in self.session.scalars(
                select(Artifact).where(
                    Artifact.kind == ArtifactKind.TTS_SEGMENT.value,
                    Artifact.status == ArtifactStatus.READY.value,
                )
            )
            if (artifact.metadata_json or {}).get("speech_segment_id") == speech_segment_id
        )

    def _ready_reused_artifacts(self, chapter_ids: set[str]) -> set[str]:
        if not chapter_ids:
            return set()
        return {
            artifact.id
            for artifact in self.session.scalars(
                select(Artifact).where(
                    Artifact.chapter_id.in_(chapter_ids),
                    Artifact.kind.in_(
                        (
                            ArtifactKind.TTS_SEGMENT.value,
                            ArtifactKind.MASTER_MP3.value,
                            ArtifactKind.SRT.value,
                        )
                    ),
                    Artifact.status == ArtifactStatus.READY.value,
                )
            )
            if artifact.id not in self._invalidated
        }

    def _changed_chapter_ids(self, change: Change) -> set[str]:
        if change.kind is ChangeKind.SOURCE_REVISION:
            return {chapter.id for chapter in self._chapters_for_source_revisions(change.affected_ids)}
        if change.kind in {
            ChangeKind.PROJECT_METADATA,
            ChangeKind.GLOSSARY,
            ChangeKind.STORY_MEMORY,
            ChangeKind.MASTER_SETTINGS,
            ChangeKind.RIGHTS_EVIDENCE,
        }:
            return {chapter.id for chapter in self._chapters_for_projects_or_chapters(change.affected_ids)}
        if change.kind is ChangeKind.TARGET_TEXT:
            return {segment.chapter_id for segment in self._speech_for_translation_segments(change.affected_ids)}
        if change.kind is ChangeKind.PRONUNCIATION:
            return {segment.chapter_id for segment in self._speech_for_speech_or_chapter(change.affected_ids)}
        if change.kind is ChangeKind.VOICE_ROLE:
            return {segment.chapter_id for segment in self._speech_for_voice_roles(change.affected_ids)}
        return set()

    def _chapters_for_source_revisions(self, affected_ids: tuple[str, ...]) -> tuple[Chapter, ...]:
        chapters: list[Chapter] = []
        for revision_id in affected_ids:
            revision = self.session.get(SourceRevision, revision_id)
            if revision is None:
                continue
            chapter = self.session.get(Chapter, revision.chapter_id)
            if chapter is not None:
                chapters.append(chapter)
        return tuple(_unique_by_id(chapters))

    def _chapters_for_projects_or_chapters(self, affected_ids: tuple[str, ...]) -> tuple[Chapter, ...]:
        chapters: list[Chapter] = []
        for affected_id in affected_ids:
            chapter = self.session.get(Chapter, affected_id)
            if chapter is not None:
                chapters.append(chapter)
                continue
            project = self.session.get(Project, affected_id)
            if project is not None:
                chapters.extend(
                    self.session.scalars(
                        select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.ordinal, Chapter.id)
                    )
                )
        return tuple(_unique_by_id(chapters))

    def _speech_for_translation_segments(self, affected_ids: tuple[str, ...]) -> tuple[SpeechSegment, ...]:
        return tuple(
            self.session.scalars(
                select(SpeechSegment)
                .where(SpeechSegment.translation_segment_id.in_(affected_ids))
                .order_by(SpeechSegment.segment_index, SpeechSegment.id)
            )
        )

    def _speech_for_speech_or_chapter(self, affected_ids: tuple[str, ...]) -> tuple[SpeechSegment, ...]:
        segments: list[SpeechSegment] = []
        for affected_id in affected_ids:
            speech = self.session.get(SpeechSegment, affected_id)
            if speech is not None:
                segments.append(speech)
                continue
            chapter = self.session.get(Chapter, affected_id)
            if chapter is not None:
                segments.extend(
                    self.session.scalars(
                        select(SpeechSegment)
                        .where(SpeechSegment.chapter_id == chapter.id)
                        .order_by(SpeechSegment.segment_index, SpeechSegment.id)
                    )
                )
        return tuple(_unique_by_id(segments))

    def _speech_for_voice_roles(self, affected_ids: tuple[str, ...]) -> tuple[SpeechSegment, ...]:
        plan_ids = {
            plan.id
            for plan in (
                self.session.get(VoicePlan, affected_id) for affected_id in affected_ids
            )
            if plan is not None
        }
        direct = list(
            self.session.scalars(
                select(SpeechSegment)
                .where(SpeechSegment.role_id.in_(affected_ids))
                .order_by(SpeechSegment.segment_index, SpeechSegment.id)
            )
        )
        if plan_ids:
            direct.extend(
                self.session.scalars(
                    select(SpeechSegment)
                    .where(SpeechSegment.voice_plan_id.in_(plan_ids))
                    .order_by(SpeechSegment.segment_index, SpeechSegment.id)
                )
            )
        return tuple(_unique_by_id(direct))


def _unique_by_id(items):
    seen: set[str] = set()
    unique = []
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        unique.append(item)
    return unique
