from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from app.contracts import ArtifactKind, JobKind, JobStatus, new_id


COMMON_PREVIEW_TEXT = (
    "Day la doan nghe thu giong doc chung cho moi preset. Giong doc can ro rang, am ap, "
    "giu nhip ke chuyen on dinh va phu hop voi tieu thuyet audio tieng Viet."
)


class VoiceCatalogError(Exception):
    """Base voice catalog exception."""


class ModelLicenseUnverified(VoiceCatalogError):
    """Raised when a voice is selected before model and license snapshots are verified."""


class LocalModelUnavailable(VoiceCatalogError):
    """Raised when a local voice cannot be activated from local verified files."""


@dataclass(frozen=True)
class VoicePreset:
    id: str
    name: str
    provider: str
    model: str
    locale: str
    region: str
    gender: str
    license: str
    cost_tier: str
    sample_rate: int
    model_path: Path
    license_snapshot_path: Path
    model_sha256: str
    license_snapshot_sha256: str
    model_verified: bool
    license_verified: bool
    poc_passed: bool
    settings_hash: str = "default"
    pronunciation_hash: str = "default"


@dataclass(frozen=True)
class VoiceView:
    id: str
    name: str
    provider: str
    model: str
    locale: str
    region: str
    gender: str
    license: str
    cost_tier: str
    online: bool
    favorite: bool
    available: bool
    active: bool
    activation_hint: str


@dataclass(frozen=True)
class JobView:
    id: str
    status: str
    kind: str
    artifact_kind: str
    cache_key: str
    preset_id: str
    text: str


class VoiceCatalog:
    def __init__(self, presets: tuple[VoicePreset, ...], *, active_preset_id: str | None = None) -> None:
        self._presets = presets
        self._active_preset_id = active_preset_id

    @classmethod
    def local_defaults(cls, data_root: Path) -> VoiceCatalog:
        model_root = Path(data_root) / "models" / "voices"
        return cls(
            presets=(
                VoicePreset(
                    id="vieneu-vi-int8",
                    name="VieNeu Vietnamese",
                    provider="vieneu",
                    model="vieneu-vi-int8",
                    locale="vi-VN",
                    region="local",
                    gender="neutral",
                    license="verified local model snapshot",
                    cost_tier="local",
                    sample_rate=44_100,
                    model_path=model_root / "vieneu" / "vieneu-vi-int8.onnx",
                    license_snapshot_path=model_root / "vieneu" / "LICENSE.snapshot.txt",
                    model_sha256="",
                    license_snapshot_sha256="",
                    model_verified=False,
                    license_verified=False,
                    poc_passed=False,
                ),
                VoicePreset(
                    id="piper-vais1000",
                    name="Piper vais1000",
                    provider="piper",
                    model="vais1000",
                    locale="vi-VN",
                    region="local",
                    gender="neutral",
                    license="CC-BY-4.0",
                    cost_tier="local",
                    sample_rate=22_050,
                    model_path=model_root / "piper" / "vais1000.onnx",
                    license_snapshot_path=model_root / "piper" / "LICENSE.snapshot.txt",
                    model_sha256="",
                    license_snapshot_sha256="",
                    model_verified=False,
                    license_verified=False,
                    poc_passed=True,
                ),
            ),
        )

    def list(self, locale: str = "vi-VN") -> tuple[VoiceView, ...]:
        active_preset_id = self._active_preset_id or self._default_available_id(locale)
        return tuple(self._view(preset, active_preset_id) for preset in self._presets if preset.locale == locale)

    def activate_default(self, locale: str = "vi-VN") -> VoicePreset:
        active_preset_id = self._default_available_id(locale)
        if active_preset_id is not None:
            self._active_preset_id = active_preset_id
            return self._find(active_preset_id)
        raise LocalModelUnavailable("Install local model snapshot, then verify the model and license.")

    def activate(self, preset_id: str) -> VoicePreset:
        preset = self._find(preset_id)
        if not preset.model_verified or not preset.license_verified:
            raise ModelLicenseUnverified("MODEL_LICENSE_SNAPSHOT_UNVERIFIED")
        if not self._is_available(preset):
            raise LocalModelUnavailable(self._activation_hint(preset))
        self._active_preset_id = preset.id
        return preset

    def preview(self, preset_id: str, text: str) -> JobView:
        preset = self.activate(preset_id)
        return JobView(
            id=new_id(),
            status=JobStatus.QUEUED.value,
            kind=JobKind.PREVIEW_TTS.value,
            artifact_kind=ArtifactKind.VOICE_PREVIEW.value,
            cache_key=preview_cache_key(
                text=text,
                preset_id=preset.id,
                settings_hash=preset.settings_hash,
                model_hash=preset.model_sha256,
                pronunciation_hash=preset.pronunciation_hash,
            ),
            preset_id=preset.id,
            text=text,
        )

    def _find(self, preset_id: str) -> VoicePreset:
        for preset in self._presets:
            if preset.id == preset_id:
                return preset
        raise KeyError(preset_id)

    def _view(self, preset: VoicePreset, active_preset_id: str | None) -> VoiceView:
        available = self._is_available(preset)
        return VoiceView(
            id=preset.id,
            name=preset.name,
            provider=preset.provider,
            model=preset.model,
            locale=preset.locale,
            region=preset.region,
            gender=preset.gender,
            license=preset.license,
            cost_tier=preset.cost_tier,
            online=False,
            favorite=available and preset.provider == "vieneu",
            available=available,
            active=active_preset_id == preset.id,
            activation_hint=self._activation_hint(preset),
        )

    def _default_available_id(self, locale: str) -> str | None:
        candidates = [preset for preset in self._presets if preset.locale == locale]
        for provider in ("vieneu", "piper"):
            for preset in candidates:
                if preset.provider == provider and self._is_available(preset):
                    return preset.id
        return None

    def _is_available(self, preset: VoicePreset) -> bool:
        if not preset.model_verified or not preset.license_verified:
            return False
        if preset.provider == "vieneu" and not preset.poc_passed:
            return False
        if not _path_matches_sha256(preset.model_path, preset.model_sha256):
            return False
        return _path_matches_sha256(preset.license_snapshot_path, preset.license_snapshot_sha256)

    def _activation_hint(self, preset: VoicePreset) -> str:
        if not preset.model_path.is_file():
            return "Install local model snapshot, then verify the model and license."
        if not preset.license_snapshot_path.is_file():
            return "Add the local license snapshot before activating this voice."
        if not preset.model_verified or not preset.license_verified:
            return "Verify the model and license snapshots before activating this voice."
        if preset.provider == "vieneu" and not preset.poc_passed:
            return "Run and pass the VieNeu POC before making VieNeu the default voice."
        if not _path_matches_sha256(preset.model_path, preset.model_sha256):
            return "Model snapshot hash mismatch; refresh verification before activating."
        if not _path_matches_sha256(preset.license_snapshot_path, preset.license_snapshot_sha256):
            return "License snapshot hash mismatch; refresh verification before activating."
        return "Ready"


def preview_cache_key(
    *,
    text: str,
    preset_id: str,
    settings_hash: str,
    model_hash: str,
    pronunciation_hash: str,
) -> str:
    payload = {
        "text": text,
        "preset_id": preset_id,
        "settings_hash": settings_hash,
        "model_hash": model_hash,
        "pronunciation_hash": pronunciation_hash,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _path_matches_sha256(path: Path, expected_sha256: str) -> bool:
    if not expected_sha256 or not path.is_file():
        return False
    return hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
