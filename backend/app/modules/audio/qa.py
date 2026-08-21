from __future__ import annotations

from dataclasses import dataclass

from app.contracts import MasterResult, QaCategory, QaSeverity


@dataclass(frozen=True)
class AudioIssueDraft:
    category: QaCategory
    severity: QaSeverity
    evidence: str
    suggestion: str
    rule_or_model: str = "audio-qa-v1"


def run_master_qa(probe: MasterResult) -> tuple[AudioIssueDraft, ...]:
    issues: list[AudioIssueDraft] = []
    duration_seconds = probe.duration_ms / 1000
    if duration_seconds < 0.5 or duration_seconds > 120:
        issues.append(
            AudioIssueDraft(
                category=QaCategory.TTS_LENGTH,
                severity=QaSeverity.MAJOR,
                evidence=f"duration_seconds={duration_seconds:.3f}",
                suggestion="Regenerate audio with a duration between 0.5 and 120 seconds.",
            )
        )
    if probe.true_peak_dbtp > -1.0:
        issues.append(
            AudioIssueDraft(
                category=QaCategory.CLIPPING,
                severity=QaSeverity.MAJOR,
                evidence=f"true_peak_dbtp={probe.true_peak_dbtp:.2f}",
                suggestion="Lower gain or remaster before approval.",
            )
        )
    if probe.codec != "mp3" or probe.sample_rate != 44_100 or probe.channels != 1:
        issues.append(
            AudioIssueDraft(
                category=QaCategory.AUDIO_TECHNICAL,
                severity=QaSeverity.CRITICAL,
                evidence=(
                    f"codec={probe.codec}, sample_rate={probe.sample_rate}, "
                    f"channels={probe.channels}"
                ),
                suggestion="Master as mono 44.1 kHz MP3.",
            )
        )
    return tuple(issues)
