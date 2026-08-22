from __future__ import annotations

import hashlib
from pathlib import Path

from app.contracts import SynthesisResult, Usage, UsageUnit
from app.db.models import SpeechSegment
from app.modules.speech.workflow import SpeechWorkflow
from app.modules.voices.roles import AssistedVoicePlanService
from tests.speech.test_single_narrator import DeterministicAudioProcessor, _approved_chapter, _voice_preset
from tests.voices.test_roles import _extra_preset


def test_changing_one_role_reuses_other_role_audio(
    db_session,
    tmp_path: Path,
    deterministic_uuid7_factory,
) -> None:
    fixture = _approved_chapter(db_session)
    narrator = _voice_preset(db_session)
    hero = _extra_preset(db_session, 2)
    new_hero = _extra_preset(db_session, 3)
    roles = AssistedVoicePlanService(db_session, id_factory=deterministic_uuid7_factory)
    base = roles.create_multi(
        fixture.chapter_id,
        narrator_preset_id=narrator.id,
        roles=(("hero", "Hero", hero.id),),
    )
    assigned = roles.assign_roles(
        base.id,
        expected_hash=base.plan_sha256,
        assignments={base.segments[0].id: "hero"},
    )
    first_tts = VoiceRecordingTts()
    workflow = SpeechWorkflow(
        db_session,
        tts=first_tts,
        audio_processor=DeterministicAudioProcessor(),
        artifact_root=tmp_path,
        id_factory=deterministic_uuid7_factory,
    )

    first = workflow.enqueue_render(fixture.chapter_id)

    assert set(first.rendered_segment_ids) == {segment.id for segment in assigned.segments}
    assert first_tts.voice_by_segment[assigned.segments[0].id] == "voice-2"
    assert {
        first_tts.voice_by_segment[segment.id] for segment in assigned.segments[1:]
    } == {"voice-1"}

    revised = roles.change_role_voice(assigned.id, "hero", new_hero.id)
    second_tts = VoiceRecordingTts()
    workflow = SpeechWorkflow(
        db_session,
        tts=second_tts,
        audio_processor=DeterministicAudioProcessor(),
        artifact_root=tmp_path,
        id_factory=deterministic_uuid7_factory,
    )

    second = workflow.enqueue_render(fixture.chapter_id)

    revised_segments = db_session.query(SpeechSegment).filter_by(voice_plan_id=revised.id).all()
    hero_segment_ids = {
        segment.id
        for segment in revised_segments
        if next(role for role in revised.roles if role.id == segment.role_id).role_key == "hero"
    }
    narrator_segment_ids = {segment.id for segment in revised_segments} - hero_segment_ids
    assert set(second.rendered_segment_ids) == hero_segment_ids
    assert set(second.reused_segment_ids) == narrator_segment_ids
    assert set(second_tts.voice_by_segment) == hero_segment_ids
    assert set(second.segment_ids) == {segment.id for segment in revised_segments}


class VoiceRecordingTts:
    def __init__(self) -> None:
        self.voice_by_segment: dict[str, str] = {}

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
        self.voice_by_segment[request.speech_segment_id] = request.voice_id
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = f"wav:{request.speech_segment_id}:{request.voice_id}:{request.narration_text}".encode()
        output_path.write_bytes(payload)
        return SynthesisResult(
            provider="fake",
            model="fake-tts",
            provider_version="1",
            duration_ms=30_000,
            sha256=hashlib.sha256(payload).hexdigest(),
            usage=(Usage(UsageUnit.AUDIO_SECOND.value, 30),),
        )
