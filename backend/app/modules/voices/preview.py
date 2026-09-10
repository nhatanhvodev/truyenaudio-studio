from __future__ import annotations

from collections.abc import Callable, Sequence
import asyncio
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tempfile
import unicodedata

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts import (
    ArtifactKind,
    OperationContext,
    SynthesisRequest,
    SynthesisResult,
    TtsAdapter,
    new_id,
)
from app.db.models import VoicePreviewJob, VoicePreviewStatus, VoicePreviewTextKind
from app.modules.artifacts.store import (
    ArtifactAlreadyExists,
    ArtifactStore,
    ArtifactWrite,
    StoredArtifact,
)
from app.modules.voices.catalog import VoiceCatalog, VoicePreset
from app.providers.piper import PiperTtsAdapter
from app.providers.vieneu import VieNeuTtsAdapter


PREVIEW_TEXT_MAX_CHARS = 420
PREVIEW_SYNTHESIS_TIMEOUT_SECONDS = 60
PREVIEW_ENGINE_VERSION = "voice-preview-v1"
ZERO_HASH = "0" * 64

PREVIEW_TEXT_EMPTY = "PREVIEW_TEXT_EMPTY"
PREVIEW_TEXT_TOO_LONG = "PREVIEW_TEXT_TOO_LONG"
PREVIEW_TEXT_KIND_INVALID = "PREVIEW_TEXT_KIND_INVALID"
PREVIEW_COMPARE_PRESET_COUNT = "PREVIEW_COMPARE_PRESET_COUNT"
PREVIEW_COMPARE_DUPLICATE_PRESET = "PREVIEW_COMPARE_DUPLICATE_PRESET"
VOICE_PRESET_NOT_FOUND = "VOICE_PRESET_NOT_FOUND"
PREVIEW_JOB_NOT_FOUND = "PREVIEW_JOB_NOT_FOUND"
PREVIEW_NOT_READY = "PREVIEW_NOT_READY"
PREVIEW_CANCELLED = "PREVIEW_CANCELLED"
PREVIEW_AUDIO_MISSING = "PREVIEW_AUDIO_MISSING"
PREVIEW_AUDIO_EMPTY = "PREVIEW_AUDIO_EMPTY"
PREVIEW_AUDIO_CHECKSUM_MISMATCH = "PREVIEW_AUDIO_CHECKSUM_MISMATCH"
PREVIEW_ARTIFACT_CONFLICT = "PREVIEW_ARTIFACT_CONFLICT"
PREVIEW_SYNTHESIS_FAILED = "PREVIEW_SYNTHESIS_FAILED"
PREVIEW_TIMEOUT = "PREVIEW_TIMEOUT"
VOICE_MODEL_UNAVAILABLE = "VOICE_MODEL_UNAVAILABLE"
VOICE_PROVIDER_UNSUPPORTED = "VOICE_PROVIDER_UNSUPPORTED"

ADAPTER_UNAVAILABLE_ENGINE: dict[str, str] = {
    "provider": "unavailable",
    "model": "unavailable",
    "engine": "unavailable",
    "version": "unavailable",
}

AdapterFactory = Callable[[VoicePreset], TtsAdapter]


class PreviewRequestInvalid(ValueError):
    """Raised when preview input violates the preview contract (HTTP 400)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class VoicePresetMissing(LookupError):
    """Raised when a preview targets a preset that the local catalog does not expose."""

    def __init__(self, preset_id: str) -> None:
        super().__init__(VOICE_PRESET_NOT_FOUND)
        self.preset_id = preset_id


class PreviewJobMissing(LookupError):
    """Raised when a preview job id is unknown."""

    def __init__(self, job_id: str) -> None:
        super().__init__(PREVIEW_JOB_NOT_FOUND)
        self.job_id = job_id


class PreviewContentUnavailable(RuntimeError):
    """Raised when preview audio cannot be served, with a user-facing reason code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class VoiceAdapterUnavailable(RuntimeError):
    """Raised when no local adapter can serve the preset provider."""

    def __init__(self, provider: str) -> None:
        super().__init__(VOICE_PROVIDER_UNSUPPORTED)
        self.provider = provider


class PreviewSynthesisFailed(RuntimeError):
    """Raised when synthesis produced no usable audio; the job records the reason code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VoicePreviewJobView:
    job_id: str
    preset_id: str
    text_kind: str
    status: str
    cache_key: str
    from_cache: bool
    audio_url: str | None
    duration_ms: int | None
    reason: str | None


@dataclass(frozen=True)
class VoicePreviewCompareView:
    text: str
    text_sha256: str
    jobs: tuple[VoicePreviewJobView, ...]


def normalize_preview_text(text: str) -> str:
    """NFC-normalise then strip; length limits apply to the normalised text."""
    normalized = unicodedata.normalize("NFC", text or "").strip()
    if not normalized:
        raise PreviewRequestInvalid(PREVIEW_TEXT_EMPTY)
    if len(normalized) > PREVIEW_TEXT_MAX_CHARS:
        raise PreviewRequestInvalid(PREVIEW_TEXT_TOO_LONG)
    return normalized


def preview_text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def preview_content_url(job_id: str) -> str:
    return f"/api/voices/preview-jobs/{job_id}/content"


def preview_settings_hash(
    *,
    preset: VoicePreset,
    engine: dict[str, str],
) -> str:
    """Hash every non-text synthesis setting that can change the rendered audio."""
    return _canonical_sha256(
        {
            "settingsHash": preset.settings_hash,
            "pronunciationHash": preset.pronunciation_hash,
            "modelSha256": preset.model_sha256 or ZERO_HASH,
            "engine": engine,
            "sampleRate": preset.sample_rate,
        }
    )


def preview_cache_key(
    *,
    preset: VoicePreset,
    engine: dict[str, str],
    text: str,
    text_kind: str,
) -> str:
    """Cache key over preset, active settings revision, engine/version, rate, text and kind."""
    return _canonical_sha256(
        {
            "version": PREVIEW_ENGINE_VERSION,
            "presetId": preset.id,
            "settings": preview_settings_hash(preset=preset, engine=engine),
            "engine": engine,
            "sampleRate": preset.sample_rate,
            "text": text,
            "textKind": text_kind,
        }
    )


def default_adapter_factory(data_root: Path) -> AdapterFactory:
    """Build the real local adapters; a missing model fails the job, it is never faked."""
    model_root = Path(data_root) / "models" / "voices"

    def build(preset: VoicePreset) -> TtsAdapter:
        if preset.provider == "vieneu":
            return VieNeuTtsAdapter(
                binary_path=model_root / "vieneu" / "vieneu-tts",
                model_path=preset.model_path,
                model_sha256=preset.model_sha256,
            )
        if preset.provider == "piper":
            return PiperTtsAdapter(
                binary_path=model_root / "piper" / "piper",
                model_path=preset.model_path,
                config_path=model_root / "piper" / "config.json",
                model_sha256=preset.model_sha256,
            )
        raise VoiceAdapterUnavailable(preset.provider)

    return build


class VoicePreviewService:
    """Durable preview jobs: one cached job row per cache key, artifacts published atomically."""

    def __init__(
        self,
        session: Session,
        *,
        catalog: VoiceCatalog,
        artifact_root: Path | str,
        adapter_factory: AdapterFactory | None = None,
        id_factory: Callable[[], str] = new_id,
        run_inline: bool = True,
    ) -> None:
        self.session = session
        self.catalog = catalog
        self.artifact_root = Path(artifact_root)
        self.adapter_factory = adapter_factory or default_adapter_factory(
            Path(artifact_root).parent
        )
        self.id_factory = id_factory
        self.run_inline = run_inline

    def request_preview(
        self,
        preset_id: str,
        text: str,
        text_kind: str = VoicePreviewTextKind.CUSTOM.value,
    ) -> VoicePreviewJobView:
        preset = self._preset(preset_id)
        normalized = normalize_preview_text(text)
        kind = self._text_kind(text_kind)
        adapter, engine = self._prepare(preset)
        cache_key = preview_cache_key(
            preset=preset, engine=engine, text=normalized, text_kind=kind
        )
        existing = self._by_cache_key(cache_key)
        if existing is not None and existing.status == VoicePreviewStatus.READY.value:
            return self._view(existing, from_cache=True)
        job = existing or self._create_job(
            preset=preset,
            text=normalized,
            text_kind=kind,
            cache_key=cache_key,
        )
        if job.status == VoicePreviewStatus.READY.value:
            # A concurrent request won the race and already published the audio.
            return self._view(job, from_cache=True)
        if not self.run_inline:
            return self._view(job, from_cache=False)
        job.status = VoicePreviewStatus.QUEUED.value
        job.reason = None
        self.session.commit()
        self._run(job, preset=preset, adapter=adapter, engine=engine)
        return self._view(job, from_cache=False)

    def compare(self, preset_ids: Sequence[str], text: str) -> VoicePreviewCompareView:
        normalized = normalize_preview_text(text)
        ids = tuple(preset_ids)
        if len(ids) < 2 or len(ids) > 3:
            raise PreviewRequestInvalid(PREVIEW_COMPARE_PRESET_COUNT)
        if len(set(ids)) != len(ids):
            raise PreviewRequestInvalid(PREVIEW_COMPARE_DUPLICATE_PRESET)
        for preset_id in ids:
            self._preset(preset_id)
        jobs = tuple(
            self.request_preview(preset_id, normalized, VoicePreviewTextKind.CUSTOM.value)
            for preset_id in ids
        )
        return VoicePreviewCompareView(
            text=normalized,
            text_sha256=preview_text_sha256(normalized),
            jobs=jobs,
        )

    def get_job(self, job_id: str) -> VoicePreviewJobView:
        job = self._job(job_id)
        return self._view(job, from_cache=self._is_ready(job))

    def cancel_job(self, job_id: str) -> VoicePreviewJobView:
        job = self._job(job_id)
        if job.status in (
            VoicePreviewStatus.QUEUED.value,
            VoicePreviewStatus.RUNNING.value,
        ):
            job.status = VoicePreviewStatus.CANCELLED.value
            job.reason = None
            self.session.commit()
        return self._view(job, from_cache=False)

    def retry_job(self, job_id: str) -> VoicePreviewJobView:
        job = self._job(job_id)
        if job.status not in (
            VoicePreviewStatus.FAILED.value,
            VoicePreviewStatus.CANCELLED.value,
        ):
            return self._view(job, from_cache=self._is_ready(job))
        preset = self._preset(job.preset_id)
        adapter, engine = self._prepare(preset)
        self.run_job(job.id, preset=preset, adapter=adapter, engine=engine)
        return self._view(job, from_cache=False)

    def run_job(
        self,
        job_id: str,
        *,
        preset: VoicePreset | None = None,
        adapter: TtsAdapter | None = None,
        engine: dict[str, str] | None = None,
    ) -> VoicePreviewJobView:
        """Drain one job; the worker seam for QUEUED rows created with run_inline=False."""
        job = self._job(job_id)
        if job.status == VoicePreviewStatus.READY.value:
            return self._view(job, from_cache=True)
        active_preset = preset or self._preset(job.preset_id)
        if adapter is None or engine is None:
            adapter, engine = self._prepare(active_preset)
        self._run(job, preset=active_preset, adapter=adapter, engine=engine)
        return self._view(job, from_cache=self._is_ready(job))

    def read_content(self, job_id: str) -> bytes:
        job = self._job(job_id)
        if not self._is_ready(job):
            raise PreviewContentUnavailable(self._unavailable_code(job))
        path = ArtifactStore(self.artifact_root).resolve(job.relative_path)
        if not path.is_file():
            raise PreviewContentUnavailable(PREVIEW_AUDIO_MISSING)
        audio = path.read_bytes()
        if job.audio_sha256 and hashlib.sha256(audio).hexdigest() != job.audio_sha256:
            raise PreviewContentUnavailable(PREVIEW_AUDIO_CHECKSUM_MISMATCH)
        return audio

    def _run(
        self,
        job: VoicePreviewJob,
        *,
        preset: VoicePreset,
        adapter: TtsAdapter | None,
        engine: dict[str, str],
    ) -> None:
        job.status = VoicePreviewStatus.RUNNING.value
        job.reason = None
        self.session.commit()
        settings_hash = preview_settings_hash(preset=preset, engine=engine)
        try:
            if adapter is None:
                raise PreviewSynthesisFailed(VOICE_PROVIDER_UNSUPPORTED)
            audio, result = self._synthesize(job, preset, adapter)
            stored = self._store_audio(job=job, audio=audio, settings_hash=settings_hash)
            if stored.sha256 != result.sha256:
                raise PreviewSynthesisFailed(PREVIEW_AUDIO_CHECKSUM_MISMATCH)
            job.relative_path = stored.relative_path
            job.audio_sha256 = stored.sha256
            job.duration_ms = result.duration_ms
            job.status = VoicePreviewStatus.READY.value
        except FileNotFoundError:
            self._fail(
                job,
                VOICE_MODEL_UNAVAILABLE
                if not Path(preset.model_path).is_file()
                else PREVIEW_SYNTHESIS_FAILED,
            )
        except PreviewSynthesisFailed as exc:
            self._fail(job, exc.code)
        except TimeoutError:
            self._fail(job, PREVIEW_TIMEOUT)
        except Exception:
            self._fail(job, PREVIEW_SYNTHESIS_FAILED)
        finally:
            self.session.commit()

    def _fail(self, job: VoicePreviewJob, reason: str) -> None:
        job.status = VoicePreviewStatus.FAILED.value
        job.reason = reason
        job.relative_path = None
        job.audio_sha256 = None
        job.duration_ms = None

    def _synthesize(
        self,
        job: VoicePreviewJob,
        preset: VoicePreset,
        adapter: TtsAdapter,
    ) -> tuple[bytes, SynthesisResult]:
        request = SynthesisRequest(
            context=OperationContext(
                operation_id=f"preview:{job.id}",
                cache_key=job.cache_key,
                timeout_seconds=PREVIEW_SYNTHESIS_TIMEOUT_SECONDS,
                estimated_units=len(job.text),
                budget_authorization_id=None,
                cloud_consent_id=None,
            ),
            speech_segment_id=job.id,
            narration_text=job.text,
            locale=preset.locale,
            voice_id=preset.id,
            speed="1.0",
            pitch="0",
            style=None,
            sample_rate=preset.sample_rate,
        )
        with tempfile.TemporaryDirectory(prefix="voice-preview-") as staging:
            output_path = Path(staging) / "preview.wav"
            result = asyncio.run(adapter.synthesize(request, output_path))
            if not output_path.is_file():
                raise PreviewSynthesisFailed(PREVIEW_SYNTHESIS_FAILED)
            audio = output_path.read_bytes()
        if not audio:
            raise PreviewSynthesisFailed(PREVIEW_AUDIO_EMPTY)
        return audio, result

    def _store_audio(
        self,
        *,
        job: VoicePreviewJob,
        audio: bytes,
        settings_hash: str,
    ) -> StoredArtifact:
        store = ArtifactStore(self.artifact_root)
        relative_path = f"voice-previews/{job.cache_key}.wav"
        digest = hashlib.sha256(audio).hexdigest()
        writer = store.begin(
            ArtifactWrite(
                kind=ArtifactKind.VOICE_PREVIEW,
                relative_path=relative_path,
                input_hash=job.cache_key,
                settings_hash=settings_hash,
                mime_type="audio/wav",
            )
        )
        try:
            writer.file.write(audio)
            try:
                return writer.commit()
            except ArtifactAlreadyExists:
                if store.verify(relative_path, digest):
                    return StoredArtifact(
                        relative_path=relative_path,
                        sha256=digest,
                        byte_size=len(audio),
                    )
                raise PreviewSynthesisFailed(PREVIEW_ARTIFACT_CONFLICT) from None
        finally:
            writer.close()

    def _create_job(
        self,
        *,
        preset: VoicePreset,
        text: str,
        text_kind: str,
        cache_key: str,
    ) -> VoicePreviewJob:
        job = VoicePreviewJob(
            id=self.id_factory(),
            preset_id=preset.id,
            text_kind=text_kind,
            text_sha256=preview_text_sha256(text),
            text=text,
            cache_key=cache_key,
            status=VoicePreviewStatus.QUEUED.value,
        )
        try:
            with self.session.begin_nested():
                self.session.add(job)
                self.session.flush()
        except IntegrityError:
            concurrent = self._by_cache_key(cache_key)
            if concurrent is None:
                raise
            return concurrent
        return job

    def _prepare(self, preset: VoicePreset) -> tuple[TtsAdapter | None, dict[str, str]]:
        try:
            adapter = self.adapter_factory(preset)
        except VoiceAdapterUnavailable:
            return None, {
                **ADAPTER_UNAVAILABLE_ENGINE,
                "provider": preset.provider,
                "model": preset.model,
            }
        return adapter, _engine_descriptor(adapter, preset)

    def _preset(self, preset_id: str) -> VoicePreset:
        for preset in self._catalog_presets():
            if preset.id == preset_id:
                return preset
        raise VoicePresetMissing(preset_id)

    def _catalog_presets(self) -> tuple[VoicePreset, ...]:
        """Full preset records.

        VoiceCatalog.list() projects VoiceView rows for the picker UI (no settings/model fields);
        preview synthesis needs the underlying preset, including records the catalog reports as
        unavailable so the job can fail with a reason instead of inventing audio.
        """
        return tuple(
            preset
            for preset in getattr(self.catalog, "_presets", ())
            if isinstance(preset, VoicePreset)
        )

    def _job(self, job_id: str) -> VoicePreviewJob:
        job = self.session.get(VoicePreviewJob, job_id)
        if job is None:
            raise PreviewJobMissing(job_id)
        return job

    def _by_cache_key(self, cache_key: str) -> VoicePreviewJob | None:
        return self.session.scalars(
            select(VoicePreviewJob).where(VoicePreviewJob.cache_key == cache_key)
        ).first()

    def _text_kind(self, text_kind: str) -> str:
        try:
            return VoicePreviewTextKind(text_kind).value
        except ValueError as exc:
            raise PreviewRequestInvalid(PREVIEW_TEXT_KIND_INVALID) from exc

    def _is_ready(self, job: VoicePreviewJob) -> bool:
        return (
            job.status == VoicePreviewStatus.READY.value and bool(job.relative_path)
        )

    def _unavailable_code(self, job: VoicePreviewJob) -> str:
        if job.status == VoicePreviewStatus.CANCELLED.value:
            return PREVIEW_CANCELLED
        if job.status == VoicePreviewStatus.FAILED.value and job.reason:
            return job.reason
        return PREVIEW_NOT_READY

    def _view(self, job: VoicePreviewJob, *, from_cache: bool) -> VoicePreviewJobView:
        ready = self._is_ready(job)
        return VoicePreviewJobView(
            job_id=job.id,
            preset_id=job.preset_id,
            text_kind=job.text_kind,
            status=job.status,
            cache_key=job.cache_key,
            from_cache=from_cache and ready,
            audio_url=preview_content_url(job.id) if ready else None,
            duration_ms=job.duration_ms if ready else None,
            reason=job.reason,
        )


def _engine_descriptor(adapter: TtsAdapter, preset: VoicePreset) -> dict[str, str]:
    capabilities: dict[str, object] = {}
    reader = getattr(adapter, "capabilities", None)
    if callable(reader):
        try:
            capabilities = dict(reader() or {})
        except Exception:
            capabilities = {}
    return {
        "provider": str(capabilities.get("provider") or getattr(adapter, "provider", "unknown")),
        "model": str(capabilities.get("model") or preset.model),
        "engine": str(
            capabilities.get("engine")
            or getattr(adapter, "engine", None)
            or getattr(adapter, "provider_version", "unknown")
        ),
        "version": str(getattr(adapter, "provider_version", "unknown")),
    }


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
