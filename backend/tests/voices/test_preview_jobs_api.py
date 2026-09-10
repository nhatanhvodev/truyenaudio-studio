from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import unicodedata
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.contracts import SynthesisRequest, SynthesisResult, Usage, UsageUnit
from app.db.models import VoicePreviewJob, VoicePreviewStatus
from app.main import create_app
from app.settings.config import Settings


LOOPBACK_ORIGIN = "http://127.0.0.1:8765"
MANIFEST_SCHEMA = "truyenaudio-studio.voice-presets.v1"
VIETNAMESE_TEXT = "Chào buổi sáng, hôm nay trời đẹp và gió mát."
SAMPLE_TEXT = "Đây là đoạn văn mẫu để nghe thử giọng đọc."


class RecordingTtsAdapter:
    """Fake local adapter injected at the real TtsAdapter boundary.

    It only proves plumbing: it writes a synthetic WAV and never claims to be VieNeu quality.
    """

    provider = "vieneu"
    provider_version = "fake-preview-1"

    def __init__(self, *, sample_rate: int = 22_050) -> None:
        self.sample_rate = sample_rate
        self.calls: list[tuple[str, str, int]] = []
        self.failures_remaining = 0
        self.last_audio = b""

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": "vieneu",
            "model": "vieneu-vi-int8",
            "engine": "onnx-int8",
            "sample_rates": [self.sample_rate],
            "formats": ["wav"],
            "network": False,
        }

    async def list_voices(self, locale: str) -> list[dict[str, object]]:
        return []

    async def synthesize(self, request: SynthesisRequest, output_path: Path) -> SynthesisResult:
        self.calls.append((request.voice_id, request.narration_text, request.sample_rate))
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise RuntimeError("synthetic adapter failure")
        audio = _wav_bytes(sample_rate=request.sample_rate, frames=request.sample_rate // 5)
        self.last_audio = audio
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)
        return SynthesisResult(
            provider="vieneu",
            model="vieneu-vi-int8",
            provider_version=self.provider_version,
            duration_ms=200,
            sha256=hashlib.sha256(audio).hexdigest(),
            usage=(Usage(UsageUnit.AUDIO_SECOND.value, 1),),
        )


@dataclass
class PreviewHarness:
    client: TestClient
    adapter: RecordingTtsAdapter
    headers: dict[str, str]

    def create_job(
        self,
        preset_id: str,
        text: str,
        text_kind: str | None = None,
    ):
        body: dict[str, object] = {"presetId": preset_id, "text": text}
        if text_kind is not None:
            body["textKind"] = text_kind
        return self.client.post("/api/voices/preview-jobs", json=body, headers=self.headers)

    def compare(self, preset_ids: list[str], text: str):
        return self.client.post(
            "/api/voices/preview-jobs/compare",
            json={"presetIds": preset_ids, "text": text},
            headers=self.headers,
        )

    def get_job(self, job_id: str):
        return self.client.get(f"/api/voices/preview-jobs/{job_id}")

    def cancel(self, job_id: str):
        return self.client.post(
            f"/api/voices/preview-jobs/{job_id}/cancel", headers=self.headers
        )

    def retry(self, job_id: str):
        return self.client.post(
            f"/api/voices/preview-jobs/{job_id}/retry", headers=self.headers
        )

    def content(self, job_id: str):
        return self.client.get(f"/api/voices/preview-jobs/{job_id}/content")


@pytest.fixture
def voice_manifest(settings: Settings) -> list[dict[str, object]]:
    presets = [
        _verified_preset(settings.data_root, "vieneu-narrator-a"),
        _verified_preset(settings.data_root, "vieneu-narrator-b"),
        _verified_preset(settings.data_root, "piper-vais1000", provider="piper"),
    ]
    _write_manifest(settings.data_root, presets)
    return presets


@pytest.fixture
def harness_factory(settings: Settings, migrated_engine):
    opened: list[PreviewHarness] = []

    def build(*, inject_recording_adapter: bool = True, run_inline: bool = True) -> PreviewHarness:
        built = _harness_for(
            settings,
            inject_recording_adapter=inject_recording_adapter,
            run_inline=run_inline,
        )
        opened.append(built)
        return built

    yield build

    for built in opened:
        built.client.__exit__(None, None, None)


@pytest.fixture
def harness(harness_factory, voice_manifest: list[dict[str, object]]) -> PreviewHarness:
    return harness_factory()


@pytest.fixture
def real_adapter_harness(harness_factory, settings: Settings) -> PreviewHarness:
    # No adapter factory is injected: the service builds the real VieNeuTtsAdapter, whose model
    # file does not exist on this machine, and whose locked sample rate is 44_100 Hz.
    _write_manifest(
        settings.data_root,
        [
            _verified_preset(
                settings.data_root,
                "vieneu-missing-model",
                sample_rate=44_100,
                model_exists=False,
            )
        ],
    )
    return harness_factory(inject_recording_adapter=False)


def test_preview_job_rejects_empty_text_with_reason(harness: PreviewHarness) -> None:
    for text in ("", "   ", "\n\t  \n"):
        response = harness.create_job("vieneu-narrator-a", text)
        assert response.status_code == 400
        assert response.json()["detail"] == "PREVIEW_TEXT_EMPTY"

    assert harness.adapter.calls == []


def test_preview_job_rejects_text_longer_than_420_characters(harness: PreviewHarness) -> None:
    too_long = harness.create_job("vieneu-narrator-a", "a" * 421)
    long_after_strip = harness.create_job("vieneu-narrator-a", "  " + "a" * 421 + "  ")

    assert too_long.status_code == 400
    assert too_long.json()["detail"] == "PREVIEW_TEXT_TOO_LONG"
    assert long_after_strip.status_code == 400
    assert long_after_strip.json()["detail"] == "PREVIEW_TEXT_TOO_LONG"
    assert harness.adapter.calls == []


def test_preview_job_accepts_exactly_420_characters(harness: PreviewHarness) -> None:
    response = harness.create_job("vieneu-narrator-a", "a" * 420)

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "READY"
    assert payload["textKind"] == "CUSTOM"
    assert payload["fromCache"] is False
    assert payload["durationMs"] == 200
    assert harness.adapter.calls[0][1] == "a" * 420


def test_preview_job_normalizes_nfc_so_nfd_variant_reuses_the_same_cache(
    harness: PreviewHarness,
) -> None:
    nfc = unicodedata.normalize("NFC", VIETNAMESE_TEXT)
    nfd = unicodedata.normalize("NFD", VIETNAMESE_TEXT)
    assert nfd != nfc

    first = harness.create_job("vieneu-narrator-a", nfc)
    second = harness.create_job("vieneu-narrator-a", nfd)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["cacheKey"] == second.json()["cacheKey"]
    assert second.json()["jobId"] == first.json()["jobId"]
    assert second.json()["fromCache"] is True
    assert len(harness.adapter.calls) == 1
    # The adapter stores the NFC form, never the decomposed input.
    assert harness.adapter.calls[0][1] == nfc


def test_preview_job_lifecycle_returns_ready_view_and_audio_bytes(
    harness: PreviewHarness, settings: Settings
) -> None:
    created = harness.create_job("vieneu-narrator-a", VIETNAMESE_TEXT)
    assert created.status_code == 201
    job_id = created.json()["jobId"]

    fetched = harness.get_job(job_id)
    assert fetched.status_code == 200
    # GET on a READY job is already durable, so it reports the cache state and synthesizes nothing.
    assert fetched.json() == {**created.json(), "fromCache": True}
    assert fetched.json()["audioUrl"] == f"/api/voices/preview-jobs/{job_id}/content"
    assert harness.adapter.calls == [("vieneu-narrator-a", VIETNAMESE_TEXT, 22_050)]

    content = harness.content(job_id)
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("audio/wav")
    assert content.content == harness.adapter.last_audio

    artifact = (
        settings.data_root
        / "artifacts"
        / "voice-previews"
        / f"{created.json()['cacheKey']}.wav"
    )
    assert artifact.is_file()
    assert artifact.read_bytes() == harness.adapter.last_audio
    assert not list(artifact.parent.glob("*.partial"))


def test_preview_job_second_request_is_served_from_cache_without_synthesis(
    harness: PreviewHarness,
) -> None:
    first = harness.create_job("vieneu-narrator-a", VIETNAMESE_TEXT)
    second = harness.create_job("vieneu-narrator-a", VIETNAMESE_TEXT)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["fromCache"] is True
    assert second.json()["jobId"] == first.json()["jobId"]
    assert second.json()["cacheKey"] == first.json()["cacheKey"]
    assert second.json()["audioUrl"] == first.json()["audioUrl"]
    assert len(harness.adapter.calls) == 1


def test_preview_cache_key_changes_when_preset_changes(harness: PreviewHarness) -> None:
    first = harness.create_job("vieneu-narrator-a", VIETNAMESE_TEXT)
    second = harness.create_job("vieneu-narrator-b", VIETNAMESE_TEXT)

    assert first.json()["cacheKey"] != second.json()["cacheKey"]
    assert first.json()["jobId"] != second.json()["jobId"]
    assert second.json()["fromCache"] is False
    assert len(harness.adapter.calls) == 2
    assert [call[0] for call in harness.adapter.calls] == [
        "vieneu-narrator-a",
        "vieneu-narrator-b",
    ]


def test_preview_cache_key_changes_when_text_changes(harness: PreviewHarness) -> None:
    first = harness.create_job("vieneu-narrator-a", VIETNAMESE_TEXT)
    second = harness.create_job("vieneu-narrator-a", VIETNAMESE_TEXT + " Thêm một câu.")

    assert first.json()["cacheKey"] != second.json()["cacheKey"]
    assert second.json()["fromCache"] is False
    assert len(harness.adapter.calls) == 2


def test_preview_cache_key_changes_when_preset_settings_revision_changes(
    harness: PreviewHarness, settings: Settings
) -> None:
    first = harness.create_job("vieneu-narrator-a", VIETNAMESE_TEXT)

    _write_manifest(
        settings.data_root,
        [
            _verified_preset(settings.data_root, "vieneu-narrator-a", settings_hash="speed=1.25"),
            _verified_preset(settings.data_root, "vieneu-narrator-b"),
            _verified_preset(settings.data_root, "piper-vais1000", provider="piper"),
        ],
    )
    second = harness.create_job("vieneu-narrator-a", VIETNAMESE_TEXT)

    assert first.json()["cacheKey"] != second.json()["cacheKey"]
    assert second.json()["fromCache"] is False
    assert second.json()["jobId"] != first.json()["jobId"]
    assert len(harness.adapter.calls) == 2


def test_preview_text_kind_changes_the_cache_key(harness: PreviewHarness) -> None:
    sample = harness.create_job("vieneu-narrator-a", SAMPLE_TEXT, "SAMPLE")
    custom = harness.create_job("vieneu-narrator-a", SAMPLE_TEXT, "CUSTOM")

    assert sample.status_code == 201
    assert custom.status_code == 201
    assert sample.json()["textKind"] == "SAMPLE"
    assert custom.json()["textKind"] == "CUSTOM"
    assert sample.json()["cacheKey"] != custom.json()["cacheKey"]
    assert len(harness.adapter.calls) == 2


def test_preview_job_cancel_is_idempotent_and_blocks_content(
    harness_factory, voice_manifest: list[dict[str, object]]
) -> None:
    harness = harness_factory(run_inline=False)
    created = harness.create_job("vieneu-narrator-a", VIETNAMESE_TEXT)
    assert created.status_code == 201
    assert created.json()["status"] == "QUEUED"
    assert created.json()["audioUrl"] is None
    assert harness.adapter.calls == []

    cancelled = harness.cancel(created.json()["jobId"])
    cancelled_again = harness.cancel(created.json()["jobId"])

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled_again.status_code == 200
    assert cancelled_again.json()["status"] == "CANCELLED"
    assert cancelled_again.json()["reason"] is None

    content = harness.content(created.json()["jobId"])
    assert content.status_code == 409
    assert content.json()["detail"] == "PREVIEW_CANCELLED"
    assert harness.get_job(created.json()["jobId"]).json()["status"] == "CANCELLED"
    assert harness.adapter.calls == []


def test_preview_job_retry_after_failure_synthesizes_again(harness: PreviewHarness) -> None:
    harness.adapter.failures_remaining = 1
    created = harness.create_job("vieneu-narrator-a", VIETNAMESE_TEXT)

    assert created.status_code == 201
    assert created.json()["status"] == "FAILED"
    assert created.json()["reason"] == "PREVIEW_SYNTHESIS_FAILED"
    assert created.json()["audioUrl"] is None
    assert harness.content(created.json()["jobId"]).status_code == 409

    retried = harness.retry(created.json()["jobId"])

    assert retried.status_code == 200
    assert retried.json()["status"] == "READY"
    assert retried.json()["reason"] is None
    assert retried.json()["jobId"] == created.json()["jobId"]
    assert retried.json()["cacheKey"] == created.json()["cacheKey"]
    assert len(harness.adapter.calls) == 2
    assert harness.content(created.json()["jobId"]).status_code == 200


def test_preview_job_retry_when_ready_does_not_synthesize_again(
    harness: PreviewHarness,
) -> None:
    created = harness.create_job("vieneu-narrator-a", VIETNAMESE_TEXT)
    assert created.json()["status"] == "READY"

    retried = harness.retry(created.json()["jobId"])

    assert retried.status_code == 200
    assert retried.json()["status"] == "READY"
    assert retried.json()["jobId"] == created.json()["jobId"]
    assert len(harness.adapter.calls) == 1
    assert harness.content(created.json()["jobId"]).content == harness.adapter.last_audio


def test_preview_job_retry_while_queued_keeps_state_without_synthesis(
    harness_factory, voice_manifest: list[dict[str, object]]
) -> None:
    harness = harness_factory(run_inline=False)
    created = harness.create_job("vieneu-narrator-a", VIETNAMESE_TEXT)

    retried = harness.retry(created.json()["jobId"])

    assert retried.status_code == 200
    assert retried.json()["status"] == "QUEUED"
    assert retried.json()["fromCache"] is False
    assert harness.adapter.calls == []


def test_preview_job_cancel_then_retry_recovers_a_job_stuck_after_a_crash(
    harness_factory, voice_manifest: list[dict[str, object]], db_session
) -> None:
    """A row left RUNNING by an interrupted run is recoverable: cancel, then retry."""
    harness = harness_factory(run_inline=False)
    created = harness.create_job("vieneu-narrator-a", VIETNAMESE_TEXT)
    job_id = created.json()["jobId"]
    assert created.json()["status"] == "QUEUED"

    stuck = db_session.get(VoicePreviewJob, job_id)
    stuck.status = VoicePreviewStatus.RUNNING.value
    db_session.commit()

    assert harness.get_job(job_id).json()["status"] == "RUNNING"
    assert harness.content(job_id).status_code == 409

    cancelled = harness.cancel(job_id)
    assert cancelled.json()["status"] == "CANCELLED"

    retried = harness.retry(job_id)

    assert retried.status_code == 200
    assert retried.json()["status"] == "READY"
    assert retried.json()["audioUrl"] == f"/api/voices/preview-jobs/{job_id}/content"
    assert len(harness.adapter.calls) == 1
    assert harness.content(job_id).status_code == 200


def test_preview_job_cancel_after_ready_keeps_the_cached_audio(
    harness: PreviewHarness,
) -> None:
    created = harness.create_job("vieneu-narrator-a", VIETNAMESE_TEXT)
    job_id = created.json()["jobId"]
    assert created.json()["status"] == "READY"

    cancelled = harness.cancel(job_id)

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "READY"
    assert cancelled.json()["audioUrl"] == created.json()["audioUrl"]
    assert harness.content(job_id).content == harness.adapter.last_audio
    assert len(harness.adapter.calls) == 1


def test_preview_job_unknown_id_returns_404(harness: PreviewHarness) -> None:
    missing = "018f0000-0000-7000-8000-00000000dead"

    assert harness.get_job(missing).status_code == 404
    assert harness.get_job(missing).json()["detail"] == "PREVIEW_JOB_NOT_FOUND"
    assert harness.cancel(missing).status_code == 404
    assert harness.retry(missing).status_code == 404
    assert harness.content(missing).status_code == 404


def test_preview_job_unknown_preset_returns_404(harness: PreviewHarness) -> None:
    response = harness.create_job("vieneu-not-in-catalog", VIETNAMESE_TEXT)

    assert response.status_code == 404
    assert response.json()["detail"] == "VOICE_PRESET_NOT_FOUND"
    assert harness.adapter.calls == []


def test_preview_job_with_missing_local_model_fails_with_reason(
    real_adapter_harness: PreviewHarness,
) -> None:
    """Real adapter, no fake: an absent model must fail the job, never invent audio."""
    response = real_adapter_harness.create_job("vieneu-missing-model", VIETNAMESE_TEXT)

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "FAILED"
    assert payload["reason"] == "VOICE_MODEL_UNAVAILABLE"
    assert payload["audioUrl"] is None
    assert payload["durationMs"] is None
    assert real_adapter_harness.content(payload["jobId"]).status_code == 409


def test_preview_compare_shares_one_normalized_text_across_presets(
    harness: PreviewHarness,
) -> None:
    nfd = unicodedata.normalize("NFD", VIETNAMESE_TEXT)
    response = harness.compare(
        ["vieneu-narrator-a", "vieneu-narrator-b", "piper-vais1000"], nfd
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["text"] == unicodedata.normalize("NFC", VIETNAMESE_TEXT)
    assert payload["textSha256"] == hashlib.sha256(payload["text"].encode("utf-8")).hexdigest()
    assert len(payload["jobs"]) == 3
    assert [job["presetId"] for job in payload["jobs"]] == [
        "vieneu-narrator-a",
        "vieneu-narrator-b",
        "piper-vais1000",
    ]
    assert all(job["status"] == "READY" for job in payload["jobs"])
    assert len({job["cacheKey"] for job in payload["jobs"]}) == 3
    # Every compared job synthesized the same normalized text, once each.
    assert [call[1] for call in harness.adapter.calls] == [payload["text"]] * 3

    repeated = harness.compare(
        ["vieneu-narrator-a", "vieneu-narrator-b", "piper-vais1000"],
        VIETNAMESE_TEXT,
    )
    assert [job["jobId"] for job in repeated.json()["jobs"]] == [
        job["jobId"] for job in payload["jobs"]
    ]
    assert all(job["fromCache"] is True for job in repeated.json()["jobs"])
    assert len(harness.adapter.calls) == 3


def test_preview_compare_rejects_bad_preset_cardinality(harness: PreviewHarness) -> None:
    single = harness.compare(["vieneu-narrator-a"], VIETNAMESE_TEXT)
    duplicates = harness.compare(
        ["vieneu-narrator-a", "vieneu-narrator-a"], VIETNAMESE_TEXT
    )
    too_many = harness.compare(
        ["vieneu-narrator-a", "vieneu-narrator-b", "piper-vais1000", "vieneu-narrator-a"],
        VIETNAMESE_TEXT,
    )

    assert single.status_code == 400
    assert single.json()["detail"] == "PREVIEW_COMPARE_PRESET_COUNT"
    assert duplicates.status_code == 400
    assert duplicates.json()["detail"] == "PREVIEW_COMPARE_DUPLICATE_PRESET"
    assert too_many.status_code == 400
    assert too_many.json()["detail"] == "PREVIEW_COMPARE_PRESET_COUNT"
    assert harness.adapter.calls == []


def test_legacy_voice_routes_stay_backwards_compatible(harness: PreviewHarness) -> None:
    listing = harness.client.get("/api/voices?locale=vi-VN")
    legacy_preview = harness.client.post(
        "/api/voices/preview",
        json={"presetId": "vieneu-narrator-a", "text": VIETNAMESE_TEXT},
        headers=harness.headers,
    )

    assert listing.status_code == 200
    payload = listing.json()
    assert payload["previewText"].startswith("Khi canh cua go khép lại")
    assert [voice["id"] for voice in payload["voices"]] == [
        "vieneu-narrator-a",
        "vieneu-narrator-b",
        "piper-vais1000",
    ]
    assert legacy_preview.status_code == 200
    assert legacy_preview.json()["artifactKind"] == "VOICE_PREVIEW"
    assert legacy_preview.json()["presetId"] == "vieneu-narrator-a"


def _harness_for(
    settings: Settings,
    *,
    inject_recording_adapter: bool,
    run_inline: bool = True,
) -> PreviewHarness:
    adapter = RecordingTtsAdapter()
    app = create_app(settings=settings, acquire_lock=False)
    app.state.voice_preview_run_inline = run_inline
    if inject_recording_adapter:
        app.state.voice_preview_adapter_factory = lambda preset: adapter
    return _open_harness(app, adapter)


def _open_harness(app: FastAPI, adapter: RecordingTtsAdapter) -> PreviewHarness:
    client = TestClient(app, base_url=LOOPBACK_ORIGIN)
    client.__enter__()
    token = client.get("/api/security/bootstrap").json()["csrfToken"]
    return PreviewHarness(
        client=client,
        adapter=adapter,
        headers={"Origin": LOOPBACK_ORIGIN, "X-CSRF-Token": token},
    )


def _wav_bytes(*, sample_rate: int, frames: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x01" * frames)
    return buffer.getvalue()


def _verified_preset(
    data_root: Path,
    preset_id: str,
    *,
    provider: str = "vieneu",
    sample_rate: int = 22_050,
    settings_hash: str = "speed=1",
    pronunciation_hash: str = "names=v1",
    model_exists: bool = True,
) -> dict[str, object]:
    model_dir = data_root / "models" / "voices" / provider
    model_dir.mkdir(parents=True, exist_ok=True)
    model_bytes = f"model-{preset_id}".encode()
    license_bytes = b"license-snapshot"
    if model_exists:
        (model_dir / f"{preset_id}.onnx").write_bytes(model_bytes)
    (model_dir / f"{preset_id}.LICENSE.snapshot.txt").write_bytes(license_bytes)
    return {
        "id": preset_id,
        "name": f"Voice {preset_id}",
        "provider": provider,
        "model": f"{provider}-model",
        "locale": "vi-VN",
        "region": "local",
        "gender": "neutral",
        "license": "verified local model snapshot",
        "cost_tier": "local",
        "sample_rate": sample_rate,
        "model_path": f"models/voices/{provider}/{preset_id}.onnx",
        "license_snapshot_path": f"models/voices/{provider}/{preset_id}.LICENSE.snapshot.txt",
        "model_sha256": hashlib.sha256(model_bytes).hexdigest(),
        "license_snapshot_sha256": hashlib.sha256(license_bytes).hexdigest(),
        "model_verified": True,
        "license_verified": True,
        "poc_passed": True,
        "settings_hash": settings_hash,
        "pronunciation_hash": pronunciation_hash,
    }


def _write_manifest(data_root: Path, presets: list[dict[str, object]]) -> None:
    manifest_path = data_root / "models" / "voices" / "voice-presets.manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"schema_version": MANIFEST_SCHEMA, "presets": presets}, ensure_ascii=False),
        encoding="utf-8",
    )
