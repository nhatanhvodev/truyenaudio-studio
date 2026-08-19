from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.contracts import ArtifactKind
from app.modules.artifacts.store import ArtifactAlreadyExists, ArtifactStore, ArtifactWrite
from app.modules.poc.gates import evaluate_translation, evaluate_tts
from app.modules.poc.models import PocTranslationScore, PocTtsScore, VoiceEvidence, canonical_json


SCHEMA_VERSION = "truyenaudio-studio.poc-report.v1"
FAKE_GENERATED_AT = "2026-08-19T00:00:00+00:00"
FAKE_MODEL_HASH = hashlib.sha256(b"fake-poc-model-snapshot-v1").hexdigest()
FAKE_LICENSE_HASH = hashlib.sha256(b"synthetic offline fake provider license card").hexdigest()


def build_fake_poc_report() -> dict[str, Any]:
    translation_scores = tuple(
        PocTranslationScore(
            critical=0,
            major=1 if index in {3, 11} else 0,
            minor=index % 3,
            latency_ms=100 + index,
            cost_vnd=0,
            major_dispositions=("FIXED",) if index in {3, 11} else (),
            provider="fake",
            model="fake-mt-deterministic",
            provider_version="1.0.0",
            usage_units=128 + index,
            model_snapshot_hash=FAKE_MODEL_HASH,
            evidence_artifact_sha256=FAKE_LICENSE_HASH,
        )
        for index in range(20)
    )
    voices = tuple(
        VoiceEvidence(
            provider="vieneu",
            voice_id=f"poc-vieneu-{index}",
            model="vieneu-tts-v3-turbo",
            provider_version="3.2.9-manual-placeholder",
            model_snapshot_hash=hashlib.sha256(f"vieneu-{index}".encode("ascii")).hexdigest(),
            license_artifact_sha256=FAKE_LICENSE_HASH,
        )
        for index in range(1, 5)
    ) + (
        VoiceEvidence(
            provider="piper",
            voice_id="piper-vi-fallback",
            model="piper-vi",
            provider_version="manual-placeholder",
            model_snapshot_hash=hashlib.sha256(b"piper-vi-fallback").hexdigest(),
            license_artifact_sha256=FAKE_LICENSE_HASH,
        ),
    )
    tts_score = PocTtsScore(
        pronunciation_1_5=4.2,
        continuity_1_5=4.1,
        rtf=0.6,
        peak_working_set_mb=1024,
        duration_minutes=12,
        voices=voices,
        critical_lost_repeated_content=0,
        oom=False,
        concurrency=1,
    )
    translation_gate = evaluate_translation(translation_scores)
    tts_gate = evaluate_tts(tts_score)
    overall_passed = translation_gate.passed and tts_gate.passed

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": FAKE_GENERATED_AT,
        "provider": "fake",
        "sample": {
            "content_policy": "synthetic_noncopyrighted",
            "segment_count": len(translation_scores),
            "duration_minutes": tts_score.duration_minutes,
            "source_hash": hashlib.sha256(b"synthetic-poc-source-not-logged").hexdigest(),
        },
        "translation": {
            "provider": "fake",
            "model": "fake-mt-deterministic",
            "provider_version": "1.0.0",
            "model_snapshot_hash": FAKE_MODEL_HASH,
            "license_card_artifact": {
                "kind": "MODEL_LICENSE_SNAPSHOT",
                "sha256": FAKE_LICENSE_HASH,
                "note": "synthetic offline fake provider license card",
            },
            "rate_card_snapshot": _rate_card_snapshot("fake", "fake-mt-deterministic", "CHARACTER", 0),
            "scores": [_translation_score_json(score) for score in translation_scores],
            "gate": _gate_json(translation_gate),
        },
        "tts": {
            "provider": "fake",
            "model": "fake-tts-and-piper-evidence",
            "provider_version": "1.0.0",
            "rate_card_snapshot": _rate_card_snapshot("fake", "fake-tts-and-piper-evidence", "AUDIO_SECOND", 0),
            "voices": [
                {
                    "provider": voice.provider,
                    "voice_id": voice.voice_id,
                    "model": voice.model,
                    "provider_version": voice.provider_version,
                    "model_snapshot_hash": voice.model_snapshot_hash,
                    "license_artifact_sha256": voice.license_artifact_sha256,
                }
                for voice in voices
            ],
            "score": {
                "pronunciation_1_5": tts_score.pronunciation_1_5,
                "continuity_1_5": tts_score.continuity_1_5,
                "rtf": tts_score.rtf,
                "peak_working_set_mb": tts_score.peak_working_set_mb,
                "duration_minutes": tts_score.duration_minutes,
                "critical_lost_repeated_content": tts_score.critical_lost_repeated_content,
                "oom": tts_score.oom,
                "concurrency": tts_score.concurrency,
            },
            "gate": _gate_json(tts_gate),
        },
        "probes": {
            "qwen": {"status": "not_run", "reason": "fake provider default uses zero network"},
            "vieneu": {"status": "not_run", "reason": "requires -AllowLocalModel"},
            "ffmpeg": {"status": "not_run", "reason": "host probe is separate and redacted"},
        },
        "gates": {
            "translation": _gate_json(translation_gate),
            "tts": _gate_json(tts_gate),
            "overall": {"passed": overall_passed, "reasons": [] if overall_passed else ["one or more gates failed"]},
        },
    }


def write_poc_report(report: dict[str, Any], data_root: Path) -> dict[str, str | int]:
    payload = canonical_json(report).encode("utf-8")
    report_hash = hashlib.sha256(payload).hexdigest()
    settings_hash = hashlib.sha256(b"poc-report-settings-v1").hexdigest()
    generated_at = datetime.fromisoformat(str(report["generated_at"])).astimezone(UTC)
    relative_path = f"projects/poc/poc-report-{generated_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    store = ArtifactStore(data_root)
    report_path = store.resolve(relative_path)
    if report_path.is_file():
        existing_payload = report_path.read_bytes()
        if hashlib.sha256(existing_payload).hexdigest() != report_hash:
            raise ArtifactAlreadyExists(relative_path)
        _write_checksum(report_path, report_hash)
        return {
            "relative_path": relative_path,
            "sha256": report_hash,
            "byte_size": len(existing_payload),
            "checksum_path": str(Path(str(report_path) + ".sha256").name),
        }

    with store.begin(
        ArtifactWrite(
            kind=ArtifactKind.REPORT,
            relative_path=relative_path,
            input_hash=hashlib.sha256(b"synthetic-poc-source-not-logged").hexdigest(),
            settings_hash=settings_hash,
            mime_type="application/json",
        )
    ) as writer:
        writer.file.write(payload)
        stored = writer.commit()

    _write_checksum(report_path, report_hash)

    return {
        "relative_path": stored.relative_path,
        "sha256": stored.sha256,
        "byte_size": stored.byte_size,
        "checksum_path": str(Path(str(report_path) + ".sha256").name),
    }


def _write_checksum(report_path: Path, report_hash: str) -> None:
    checksum_path = Path(str(report_path) + ".sha256")
    checksum_payload = f"{report_hash}  {report_path.name}\n".encode("utf-8")
    if checksum_path.is_file() and checksum_path.read_bytes() == checksum_payload:
        return
    partial_checksum = checksum_path.with_name(f".{checksum_path.name}.partial")
    with partial_checksum.open("xb") as checksum_file:
        checksum_file.write(checksum_payload)
        checksum_file.flush()
    partial_checksum.replace(checksum_path)


def _rate_card_snapshot(provider: str, model: str, unit: str, price: int) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "region": "local",
        "unit": unit,
        "price_usd_micros_per_million_units": price,
        "effective_from": "2026-08-19T00:00:00+00:00",
        "verified_at": "2026-08-19T00:00:00+00:00",
        "source_url": "local://synthetic-fake-provider-rate-card",
        "source_note": "offline deterministic POC; no paid API usage",
    }


def _translation_score_json(score: PocTranslationScore) -> dict[str, Any]:
    return {
        "critical": score.critical,
        "major": score.major,
        "minor": score.minor,
        "latency_ms": score.latency_ms,
        "cost_vnd": score.cost_vnd,
        "major_dispositions": list(score.major_dispositions),
        "provider": score.provider,
        "model": score.model,
        "provider_version": score.provider_version,
        "usage_units": score.usage_units,
        "model_snapshot_hash": score.model_snapshot_hash,
        "evidence_artifact_sha256": score.evidence_artifact_sha256,
    }


def _gate_json(gate: object) -> dict[str, Any]:
    return json.loads(gate.to_canonical_json())
