from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from app.modules.poc.gates import evaluate_translation, evaluate_tts
from app.modules.poc.models import PocTranslationScore, PocTtsScore, VoiceEvidence


def _translation_score(**overrides: object) -> PocTranslationScore:
    values: dict[str, object] = {
        "critical": 0,
        "major": 0,
        "minor": 1,
        "latency_ms": 120,
        "cost_vnd": 25,
        "provider": "fake",
        "model": "fake-mt",
        "provider_version": "1.0.0",
        "usage_units": 42,
        "model_snapshot_hash": "a" * 64,
        "evidence_artifact_sha256": "b" * 64,
    }
    values.update(overrides)
    return PocTranslationScore(**values)


def _voice(name: str, provider: str = "vieneu") -> VoiceEvidence:
    return VoiceEvidence(
        provider=provider,
        voice_id=name,
        model="vieneu-tts-v3-turbo" if provider == "vieneu" else "piper-vi",
        provider_version="3.2.9" if provider == "vieneu" else "1.0.0",
        model_snapshot_hash="c" * 64,
        license_artifact_sha256="d" * 64,
    )


def _tts_score(**overrides: object) -> PocTtsScore:
    voices = tuple(_voice(f"vi-vieneu-{index}") for index in range(1, 5)) + (_voice("piper-vi-nam", "piper"),)
    values: dict[str, object] = {
        "pronunciation_1_5": 4.2,
        "continuity_1_5": 4.0,
        "rtf": 0.9,
        "peak_working_set_mb": 5200,
        "duration_minutes": 12,
        "voices": voices,
        "critical_lost_repeated_content": 0,
        "oom": False,
        "concurrency": 1,
    }
    values.update(overrides)
    return PocTtsScore(**values)


def test_translation_score_keeps_five_positional_field_constructor_compatible() -> None:
    score = PocTranslationScore(1, 0, 0, 100, 20)

    assert score.critical == 1
    assert score.cost_vnd == 20
    with pytest.raises(FrozenInstanceError):
        score.cost_vnd = 21  # type: ignore[misc]


def test_translation_gate_requires_no_critical_after_repair() -> None:
    result = evaluate_translation([PocTranslationScore(1, 0, 0, 100, 20)])

    assert not result.passed
    assert "critical" in " ".join(result.reasons).lower()


def test_translation_gate_passes_only_with_representative_scored_sample_and_evidence() -> None:
    scores = [_translation_score() for _ in range(20)]

    result = evaluate_translation(scores)

    assert result.passed
    assert result.metrics["segments"] == 20
    assert result.metrics["critical"] == 0
    assert result.metrics["cost_vnd"] == 500


def test_translation_gate_requires_20_to_30_segments_and_major_dispositions() -> None:
    too_few = [_translation_score() for _ in range(19)]
    undispositioned_major = [_translation_score(major=1) for _ in range(20)]
    dispositioned_major = [_translation_score(major=1, major_dispositions=("FIXED",)) for _ in range(20)]

    assert not evaluate_translation(too_few).passed
    assert not evaluate_translation(undispositioned_major).passed
    assert evaluate_translation(dispositioned_major).passed


def test_translation_gate_rejects_negative_metrics_or_missing_provider_evidence() -> None:
    assert not evaluate_translation([_translation_score(latency_ms=-1) for _ in range(20)]).passed
    assert not evaluate_translation([_translation_score(provider="") for _ in range(20)]).passed
    assert not evaluate_translation([_translation_score(usage_units=0) for _ in range(20)]).passed


def test_tts_gate_enforces_full_chapter_memory() -> None:
    good = _tts_score()

    assert evaluate_tts(good).passed
    assert not evaluate_tts(replace(good, peak_working_set_mb=5501)).passed


def test_tts_gate_rejects_boundary_cases_with_transparent_metrics() -> None:
    good = _tts_score()

    assert not evaluate_tts(replace(good, duration_minutes=9.99)).passed
    assert not evaluate_tts(replace(good, duration_minutes=15.01)).passed
    assert not evaluate_tts(replace(good, continuity_1_5=3.99)).passed
    assert not evaluate_tts(replace(good, critical_lost_repeated_content=1)).passed
    assert not evaluate_tts(replace(good, oom=True)).passed
    assert not evaluate_tts(replace(good, concurrency=2)).passed

    result = evaluate_tts(good)
    assert result.metrics["voices_total"] == 5
    assert result.metrics["vieneu_voices"] == 4
    assert result.metrics["piper_voices"] == 1


def test_tts_gate_requires_four_to_six_vieneu_voices_and_piper_evidence() -> None:
    good = _tts_score()
    no_piper = replace(good, voices=tuple(_voice(f"vi-vieneu-{index}") for index in range(1, 5)))
    too_few_vieneu = replace(good, voices=(_voice("one"), _voice("two"), _voice("piper", "piper")))
    missing_license = replace(
        good,
        voices=(
            _voice("one"),
            _voice("two"),
            _voice("three"),
            _voice("four"),
            replace(_voice("piper", "piper"), license_artifact_sha256=""),
        ),
    )

    assert not evaluate_tts(no_piper).passed
    assert not evaluate_tts(too_few_vieneu).passed
    assert not evaluate_tts(missing_license).passed


def test_gate_results_serialize_deterministically() -> None:
    result = evaluate_tts(_tts_score())

    first = result.to_canonical_json()
    second = result.to_canonical_json()

    assert first == second
    assert result.sha256() == result.sha256()
