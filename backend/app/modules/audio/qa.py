from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import wave

from app.contracts import MasterResult, QaCategory, QaSeverity


@dataclass(frozen=True)
class AudioIssueDraft:
    category: QaCategory
    severity: QaSeverity
    evidence: str
    suggestion: str
    rule_or_model: str = "audio-qa-v1"
    speech_segment_id: str | None = None


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


def run_premaster_qa(
    segment_paths: tuple[Path, ...],
    speech_segment_ids: tuple[str, ...],
    *,
    scene_break_segment_ids: frozenset[str] = frozenset(),
) -> tuple[AudioIssueDraft, ...]:
    issues: list[AudioIssueDraft] = []
    for path, speech_segment_id in zip(segment_paths, speech_segment_ids, strict=True):
        stats = _wav_stats(Path(path))
        if stats is None:
            continue
        if stats.peak_dbfs > -1.0:
            issues.append(
                AudioIssueDraft(
                    category=QaCategory.CLIPPING,
                    severity=QaSeverity.MAJOR,
                    evidence=f"speech_segment_id={speech_segment_id}, peak_dbfs={stats.peak_dbfs:.2f}",
                    suggestion="Regenerate the TTS segment or reduce gain before mastering.",
                    speech_segment_id=speech_segment_id,
                )
            )
        if (
            speech_segment_id not in scene_break_segment_ids
            and stats.longest_silence_seconds > 8.0
        ):
            issues.append(
                AudioIssueDraft(
                    category=QaCategory.SILENCE,
                    severity=QaSeverity.MAJOR,
                    evidence=(
                        f"speech_segment_id={speech_segment_id}, "
                        f"longest_silence_seconds={stats.longest_silence_seconds:.2f}"
                    ),
                    suggestion="Trim or regenerate silence longer than 8 seconds unless it is a scene break.",
                    speech_segment_id=speech_segment_id,
                )
            )
    return tuple(issues)


@dataclass(frozen=True)
class _WavStats:
    peak_dbfs: float
    longest_silence_seconds: float


def _wav_stats(path: Path) -> _WavStats | None:
    try:
        with wave.open(str(path), "rb") as wav:
            if wav.getsampwidth() != 2:
                return None
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (OSError, EOFError, wave.Error):
        return None

    if channels <= 0 or sample_rate <= 0 or not frames:
        return None
    samples = [
        int.from_bytes(frames[index : index + 2], byteorder="little", signed=True)
        for index in range(0, len(frames) - 1, 2)
    ]
    if not samples:
        return None
    max_abs = max(abs(sample) for sample in samples)
    peak_ratio = max_abs / 32768
    peak_dbfs = 20 * math.log10(max(peak_ratio, 1e-12))
    longest_silence_frames = _longest_silence_frames(samples, channels)
    return _WavStats(
        peak_dbfs=peak_dbfs,
        longest_silence_seconds=longest_silence_frames / sample_rate,
    )


def _longest_silence_frames(samples: list[int], channels: int) -> int:
    silence_threshold = 32
    longest = 0
    current = 0
    for index in range(0, len(samples), channels):
        frame = samples[index : index + channels]
        if len(frame) < channels:
            break
        if all(abs(sample) <= silence_threshold for sample in frame):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
