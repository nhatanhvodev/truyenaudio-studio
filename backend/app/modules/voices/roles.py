from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import ChapterState, RunStatus, VoiceMode, new_id
from app.db.base import utc_now
from app.db.models import (
    AuditEvent,
    Chapter,
    SpeechSegment,
    TranslationRun,
    TranslationSegment,
    VoicePlan,
    VoicePreset,
    VoiceRole,
)
from app.modules.projects.state_machine import next_state
from app.modules.speech.narration import derive_narration, estimate_duration_ms, narration_chunks
from app.modules.speech.workflow import SpeechSegmentView, VoicePlanView, VoiceRoleView


ZERO_HASH = "0" * 64


class VoicePlanInvalid(ValueError):
    """Raised when assisted voice-plan input violates cardinality or identity rules."""


class VoicePlanConflict(ValueError):
    """Raised when a voice-plan mutation targets a stale revision."""


@dataclass(frozen=True)
class RoleSpec:
    role_key: str
    display_name: str
    voice_preset_id: str


class AssistedVoicePlanService:
    def __init__(self, session: Session, *, id_factory: Callable[[], str] = new_id) -> None:
        self.session = session
        self.id_factory = id_factory

    def create_multi(
        self,
        chapter_id: str,
        *,
        narrator_preset_id: str,
        roles: Sequence[RoleSpec | tuple[str, str, str]],
    ) -> VoicePlanView:
        chapter = self._chapter(chapter_id)
        run = self._approved_run(chapter)
        narrator = self._voice_preset(narrator_preset_id, field="NARRATOR_REQUIRED")
        normalized_roles = self._normalize_extra_roles(tuple(_role_spec(role) for role in roles))
        if 1 + len(normalized_roles) > 4:
            raise VoicePlanInvalid("MAX_FOUR_ROLES")

        plan = VoicePlan(
            id=self.id_factory(),
            chapter_id=chapter.id,
            revision_no=self._next_plan_revision(chapter.id),
            mode=VoiceMode.ASSISTED_MULTI_VOICE.value,
            narrator_preset_id=narrator.id,
            plan_sha256=ZERO_HASH,
        )
        self.session.add(plan)
        self.session.flush()
        narrator_role = VoiceRole(
            id=self.id_factory(),
            voice_plan_id=plan.id,
            role_key="narrator",
            display_name="Narrator",
            voice_preset_id=narrator.id,
            is_narrator=True,
        )
        self.session.add(narrator_role)
        for role in normalized_roles:
            self._voice_preset(role.voice_preset_id)
            self.session.add(
                VoiceRole(
                    id=self.id_factory(),
                    voice_plan_id=plan.id,
                    role_key=role.role_key,
                    display_name=role.display_name,
                    voice_preset_id=role.voice_preset_id,
                    is_narrator=False,
                )
            )
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
                        role_id=narrator_role.id,
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
        self._audit("CREATE_ASSISTED_VOICE_PLAN", "VoicePlan", plan.id, None, plan.plan_sha256)
        self.session.commit()
        return self._plan_view(plan.id)

    def assign_roles(
        self,
        plan_id: str,
        *,
        expected_hash: str,
        assignments: Mapping[str, str],
    ) -> VoicePlanView:
        return self._revise(
            plan_id,
            expected_hash=expected_hash,
            assignments=assignments,
            role_voice_changes={},
            action="ASSIGN_VOICE_ROLES",
        )

    def change_role_voice(self, plan_id: str, role_key: str, voice_preset_id: str) -> VoicePlanView:
        source_plan = self._voice_plan(plan_id)
        return self._revise(
            plan_id,
            expected_hash=source_plan.plan_sha256,
            assignments={},
            role_voice_changes={_normalize_role_key(role_key): voice_preset_id},
            action="CHANGE_ROLE_VOICE",
        )

    def _revise(
        self,
        plan_id: str,
        *,
        expected_hash: str,
        assignments: Mapping[str, str],
        role_voice_changes: Mapping[str, str],
        action: str,
    ) -> VoicePlanView:
        source_plan = self._voice_plan(plan_id)
        if source_plan.plan_sha256 != expected_hash:
            raise VoicePlanConflict("VOICE_PLAN_HASH_STALE")
        chapter = self._chapter(source_plan.chapter_id)
        if chapter.active_voice_plan_id != source_plan.id:
            raise VoicePlanConflict("VOICE_PLAN_HASH_STALE")
        run = self._approved_run(chapter)
        source_roles = self._roles(source_plan.id)
        source_role_by_id = {role.id: role for role in source_roles}
        source_role_keys = {role.role_key for role in source_roles}
        source_segments = self._speech_segments(source_plan.id)
        unknown_segments = set(assignments) - {segment.id for segment in source_segments}
        if unknown_segments:
            raise VoicePlanInvalid("SPEECH_SEGMENT_NOT_IN_PLAN")
        unknown_roles = {_normalize_role_key(role_key) for role_key in assignments.values()} - source_role_keys
        if unknown_roles:
            raise VoicePlanInvalid("ROLE_NOT_IN_PLAN")
        normalized_voice_changes = {
            _normalize_role_key(role_key): preset_id
            for role_key, preset_id in role_voice_changes.items()
        }
        if set(normalized_voice_changes) - source_role_keys:
            raise VoicePlanInvalid("ROLE_NOT_IN_PLAN")
        for preset_id in normalized_voice_changes.values():
            self._voice_preset(preset_id)

        revised = VoicePlan(
            id=self.id_factory(),
            chapter_id=chapter.id,
            revision_no=self._next_plan_revision(chapter.id),
            mode=source_plan.mode,
            narrator_preset_id=source_plan.narrator_preset_id,
            plan_sha256=ZERO_HASH,
        )
        self.session.add(revised)
        self.session.flush()

        revised_role_ids: dict[str, str] = {}
        for role in source_roles:
            revised_role_id = self.id_factory()
            revised_role_ids[role.role_key] = revised_role_id
            self.session.add(
                VoiceRole(
                    id=revised_role_id,
                    voice_plan_id=revised.id,
                    role_key=role.role_key,
                    display_name=role.display_name,
                    voice_preset_id=normalized_voice_changes.get(role.role_key, role.voice_preset_id),
                    is_narrator=role.is_narrator,
                )
            )
        self.session.flush()

        for segment in source_segments:
            current_role = source_role_by_id[segment.role_id]
            next_role_key = _normalize_role_key(assignments.get(segment.id, current_role.role_key))
            self.session.add(
                SpeechSegment(
                    id=self.id_factory(),
                    chapter_id=segment.chapter_id,
                    translation_run_id=segment.translation_run_id,
                    voice_plan_id=revised.id,
                    segment_index=segment.segment_index,
                    translation_segment_id=segment.translation_segment_id,
                    role_id=revised_role_ids[next_role_key],
                    narration_text=segment.narration_text,
                    narration_sha256=segment.narration_sha256,
                    pause_before_ms=segment.pause_before_ms,
                    pause_after_ms=segment.pause_after_ms,
                    pronunciation_revision_hash=segment.pronunciation_revision_hash,
                    estimated_duration_ms=segment.estimated_duration_ms,
                    synthesis_cache_key=None,
                )
            )
        self.session.flush()
        revised.plan_sha256 = self._plan_hash(revised.id, run)
        chapter.active_voice_plan_id = revised.id
        self._audit(
            action,
            "VoicePlan",
            revised.id,
            source_plan.plan_sha256,
            revised.plan_sha256,
            {
                "source_plan_id": source_plan.id,
                "assigned_count": len(assignments),
                "role_voice_change_count": len(normalized_voice_changes),
            },
        )
        self.session.commit()
        return self._plan_view(revised.id)

    def _normalize_extra_roles(self, roles: tuple[RoleSpec, ...]) -> tuple[RoleSpec, ...]:
        normalized: list[RoleSpec] = []
        seen = {"narrator"}
        for role in roles:
            role_key = _normalize_role_key(role.role_key)
            if role_key in seen:
                raise VoicePlanInvalid("ROLE_KEY_NOT_UNIQUE")
            seen.add(role_key)
            normalized.append(RoleSpec(role_key, role.display_name.strip() or role_key, role.voice_preset_id))
        return tuple(normalized)

    def _plan_hash(self, plan_id: str, run: TranslationRun) -> str:
        plan = self._voice_plan(plan_id)
        roles = self._roles(plan_id)
        role_by_id = {role.id: role for role in roles}
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
                    for role in roles
                ],
                "segments": [
                    {
                        "segment_index": segment.segment_index,
                        "translation_segment_id": segment.translation_segment_id,
                        "narration_sha256": segment.narration_sha256,
                        "pronunciation_hash": segment.pronunciation_revision_hash,
                        "role_key": role_by_id[segment.role_id].role_key,
                    }
                    for segment in self._speech_segments(plan_id)
                ],
            }
        )

    def _plan_view(self, plan_id: str) -> VoicePlanView:
        plan = self._voice_plan(plan_id)
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
                    narration_diff="",
                    estimated_duration_ms=segment.estimated_duration_ms or 0,
                    synthesis_cache_key=segment.synthesis_cache_key,
                    role_id=segment.role_id,
                    role_key=role_by_id[segment.role_id].role_key,
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
            raise VoicePlanInvalid("TRANSLATION_APPROVAL_REQUIRED")
        run = self.session.get(TranslationRun, chapter.approved_translation_run_id)
        if run is None or run.status != RunStatus.APPROVED.value:
            raise VoicePlanInvalid("TRANSLATION_APPROVAL_REQUIRED")
        return run

    def _voice_preset(self, preset_id: str, *, field: str = "VOICE_PRESET_NOT_FOUND") -> VoicePreset:
        if not preset_id:
            raise VoicePlanInvalid(field)
        preset = self.session.get(VoicePreset, preset_id)
        if preset is None:
            raise VoicePlanInvalid(field)
        return preset

    def _voice_plan(self, plan_id: str) -> VoicePlan:
        plan = self.session.get(VoicePlan, plan_id)
        if plan is None:
            raise ValueError("VOICE_PLAN_NOT_FOUND")
        return plan

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

    def _next_plan_revision(self, chapter_id: str) -> int:
        current = self.session.scalar(select(func.max(VoicePlan.revision_no)).where(VoicePlan.chapter_id == chapter_id))
        return int(current or 0) + 1

    def _audit(
        self,
        action: str,
        entity_type: str,
        entity_id: str,
        before_hash: str | None,
        after_hash: str,
        details: dict[str, object] | None = None,
    ) -> None:
        self.session.add(
            AuditEvent(
                id=self.id_factory(),
                actor="LOCAL_OWNER",
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                before_hash=before_hash,
                after_hash=after_hash,
                redacted_details=json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
                created_at=utc_now(),
            )
        )


def _role_spec(value: RoleSpec | tuple[str, str, str]) -> RoleSpec:
    if isinstance(value, RoleSpec):
        return value
    return RoleSpec(value[0], value[1], value[2])


def _normalize_role_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    if not normalized:
        raise VoicePlanInvalid("ROLE_KEY_REQUIRED")
    return normalized


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
