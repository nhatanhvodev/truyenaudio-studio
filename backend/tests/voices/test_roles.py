from __future__ import annotations

import pytest

from app.contracts import ArtifactKind, ArtifactStatus, VoiceMode, VoiceOrigin
from app.db.models import Artifact, SpeechSegment, VoicePreset
from app.modules.voices.roles import AssistedVoicePlanService, VoicePlanConflict, VoicePlanInvalid
from tests.speech.test_single_narrator import _approved_chapter, _voice_preset


def test_multi_voice_requires_one_narrator_and_at_most_four_roles(
    db_session,
    deterministic_uuid7_factory,
) -> None:
    fixture = _approved_chapter(db_session)
    narrator = _voice_preset(db_session)
    service = AssistedVoicePlanService(db_session, id_factory=deterministic_uuid7_factory)

    with pytest.raises(VoicePlanInvalid, match="NARRATOR_REQUIRED"):
        service.create_multi(fixture.chapter_id, narrator_preset_id="", roles=())

    extra_presets = tuple(_extra_preset(db_session, index) for index in range(2, 6))
    with pytest.raises(VoicePlanInvalid, match="MAX_FOUR_ROLES"):
        service.create_multi(
            fixture.chapter_id,
            narrator_preset_id=narrator.id,
            roles=tuple(
                (f"role-{index}", f"Role {index}", preset.id)
                for index, preset in enumerate(extra_presets, start=1)
            ),
        )


def test_unassigned_dialogue_remains_narrator(db_session, deterministic_uuid7_factory) -> None:
    fixture = _approved_chapter(db_session)
    narrator = _voice_preset(db_session)
    second = _extra_preset(db_session, 2)
    service = AssistedVoicePlanService(db_session, id_factory=deterministic_uuid7_factory)

    plan = service.create_multi(
        fixture.chapter_id,
        narrator_preset_id=narrator.id,
        roles=(("hero", "Hero", second.id),),
    )

    assert plan.mode is VoiceMode.ASSISTED_MULTI_VOICE
    narrator_role = next(role for role in plan.roles if role.is_narrator)
    rows = db_session.query(SpeechSegment).filter_by(voice_plan_id=plan.id).all()
    assert rows
    assert all(segment.role_id == narrator_role.id for segment in rows)


def test_assign_roles_creates_new_revision_and_rejects_stale_hash(
    db_session,
    deterministic_uuid7_factory,
) -> None:
    fixture = _approved_chapter(db_session)
    narrator = _voice_preset(db_session)
    second = _extra_preset(db_session, 2)
    service = AssistedVoicePlanService(db_session, id_factory=deterministic_uuid7_factory)
    plan = service.create_multi(
        fixture.chapter_id,
        narrator_preset_id=narrator.id,
        roles=(("hero", "Hero", second.id),),
    )
    target_segment_id = plan.segments[0].id

    revised = service.assign_roles(
        plan.id,
        expected_hash=plan.plan_sha256,
        assignments={target_segment_id: "hero"},
    )

    assert revised.id != plan.id
    assert revised.plan_sha256 != plan.plan_sha256
    assert revised.segments[0].id != target_segment_id
    assert revised.segments[0].translation_segment_id == plan.segments[0].translation_segment_id
    hero = next(role for role in revised.roles if role.role_key == "hero")
    row = db_session.get(SpeechSegment, revised.segments[0].id)
    assert row is not None
    assert row.voice_plan_id == revised.id
    assert row.role_id == hero.id
    with pytest.raises(VoicePlanConflict, match="VOICE_PLAN_HASH_STALE"):
        service.assign_roles(plan.id, expected_hash=plan.plan_sha256, assignments={target_segment_id: "hero"})


def _extra_preset(db_session, index: int) -> VoicePreset:
    artifact = Artifact(
        id=f"018f0000-0000-7000-8000-72000000000{index}",
        kind=ArtifactKind.LICENSE_SNAPSHOT.value,
        status=ArtifactStatus.READY.value,
        relative_path=f"models/license-{index}.txt",
        sha256=f"{index:x}" * 64,
        byte_size=12,
        mime_type="text/plain",
        input_hash=f"{index + 1:x}" * 64,
        settings_hash=f"{index + 2:x}" * 64,
    )
    preset = VoicePreset(
        id=f"018f0000-0000-7000-8000-71000000000{index}",
        name=f"Role {index}",
        provider_voice_id=f"voice-{index}",
        locale="vi-VN",
        origin=VoiceOrigin.BUILT_IN.value,
        gender_label="neutral",
        region_label="local",
        speed="1.0",
        pitch="0",
        style=None,
        sample_rate=44_100,
        settings_json={"speed": "1.0", "pitch": "0"},
        model_snapshot_hash=f"{index + 3:x}" * 64,
        license_snapshot_artifact_id=artifact.id,
        active=True,
    )
    db_session.add_all((artifact, preset))
    db_session.commit()
    return preset
