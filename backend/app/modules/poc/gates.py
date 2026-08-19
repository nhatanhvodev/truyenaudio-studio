from __future__ import annotations

from statistics import median

from app.modules.poc.models import GateResult, PocTranslationScore, PocTtsScore, is_sha256


MIN_TRANSLATION_SEGMENTS = 20
MAX_TRANSLATION_SEGMENTS = 30
MIN_TTS_MINUTES = 10.0
MAX_TTS_MINUTES = 15.0
MAX_WORKING_SET_MB = 5500
MIN_VIENEU_VOICES = 4
MAX_VIENEU_VOICES = 6


def evaluate_translation(scores: list[PocTranslationScore] | tuple[PocTranslationScore, ...]) -> GateResult:
    reasons: list[str] = []
    segment_count = len(scores)
    critical = sum(score.critical for score in scores)
    major = sum(score.major for score in scores)
    minor = sum(score.minor for score in scores)
    latency_ms = sum(score.latency_ms for score in scores)
    cost_vnd = sum(score.cost_vnd for score in scores)
    usage_units = sum(score.usage_units for score in scores)

    if not MIN_TRANSLATION_SEGMENTS <= segment_count <= MAX_TRANSLATION_SEGMENTS:
        reasons.append("translation sample must include 20-30 scored segments")
    if critical != 0:
        reasons.append("translation critical findings must be 0 after repair")
    if any(score.major > len(score.major_dispositions) for score in scores):
        reasons.append("every translation major finding must have a disposition")
    if any(score.latency_ms < 0 or score.cost_vnd < 0 for score in scores):
        reasons.append("translation latency and cost must be nonnegative")
    if any(not _has_translation_evidence(score) for score in scores):
        reasons.append("translation provider, model, version, usage, model snapshot and evidence are required")

    return GateResult(
        passed=not reasons,
        reasons=tuple(reasons),
        metrics={
            "segments": segment_count,
            "critical": critical,
            "major": major,
            "minor": minor,
            "latency_ms": latency_ms,
            "cost_vnd": cost_vnd,
            "usage_units": usage_units,
        },
    )


def evaluate_tts(score: PocTtsScore) -> GateResult:
    reasons: list[str] = []
    vieneu_voices = [voice for voice in score.voices if voice.provider == "vieneu"]
    piper_voices = [voice for voice in score.voices if voice.provider == "piper"]
    pronunciation_median = median([score.pronunciation_1_5])
    continuity_median = median([score.continuity_1_5])

    if pronunciation_median < 4.0:
        reasons.append("TTS pronunciation median must be at least 4")
    if continuity_median < 4.0:
        reasons.append("TTS continuity median must be at least 4")
    if not MIN_TTS_MINUTES <= score.duration_minutes <= MAX_TTS_MINUTES:
        reasons.append("TTS corpus must represent the same 10-15 minute chapter sample")
    if score.critical_lost_repeated_content != 0:
        reasons.append("TTS must have no critical lost or repeated content")
    if score.oom:
        reasons.append("TTS run must not OOM")
    if score.peak_working_set_mb > MAX_WORKING_SET_MB:
        reasons.append("TTS full process-tree working set must be <= 5500 MB")
    if score.peak_working_set_mb < 0 or score.rtf < 0:
        reasons.append("TTS memory and RTF metrics must be nonnegative")
    if score.concurrency != 1:
        reasons.append("TTS concurrency must remain 1")
    if not MIN_VIENEU_VOICES <= len(vieneu_voices) <= MAX_VIENEU_VOICES:
        reasons.append("TTS evidence must include 4-6 VieNeu voices")
    if len(piper_voices) < 1:
        reasons.append("TTS evidence must include Piper fallback")
    if any(not _has_voice_evidence(voice) for voice in score.voices):
        reasons.append("TTS voice evidence requires provider, voice, model, version, snapshot and license hash")

    return GateResult(
        passed=not reasons,
        reasons=tuple(reasons),
        metrics={
            "pronunciation_median": pronunciation_median,
            "continuity_median": continuity_median,
            "rtf": score.rtf,
            "peak_working_set_mb": score.peak_working_set_mb,
            "duration_minutes": score.duration_minutes,
            "voices_total": len(score.voices),
            "vieneu_voices": len(vieneu_voices),
            "piper_voices": len(piper_voices),
            "critical_lost_repeated_content": score.critical_lost_repeated_content,
            "oom": score.oom,
            "concurrency": score.concurrency,
        },
    )


def _has_translation_evidence(score: PocTranslationScore) -> bool:
    return (
        bool(score.provider.strip())
        and bool(score.model.strip())
        and bool(score.provider_version.strip())
        and score.usage_units > 0
        and is_sha256(score.model_snapshot_hash)
        and is_sha256(score.evidence_artifact_sha256)
    )


def _has_voice_evidence(voice: object) -> bool:
    return all(
        bool(getattr(voice, field_name).strip())
        for field_name in ("provider", "voice_id", "model", "provider_version")
    ) and is_sha256(getattr(voice, "model_snapshot_hash")) and is_sha256(getattr(voice, "license_artifact_sha256"))
