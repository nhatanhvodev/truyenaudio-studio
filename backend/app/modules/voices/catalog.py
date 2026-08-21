from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from app.contracts import ArtifactKind, JobKind, JobStatus, new_id


COMMON_PREVIEW_TEXT = (
    "Khi canh cua go khép lại, Minh dừng trước hiên nhà và lắng nghe tiếng mưa rơi xuống mái ngói. "
    "Ngoài con ngõ nhỏ, ánh đèn vàng trải thành từng vệt mỏng, lúc sáng, lúc tối, như đang thở cùng thành phố. "
    "Cậu mở cuốn sổ cũ, đọc chậm từng dòng ghi chú, rồi mỉm cười khi nhận ra bí mật tưởng đã mất vẫn nằm ở trang cuối. "
    "Giọng kể cần giữ nhịp bình tĩnh, rõ chữ, có khoảng nghỉ tự nhiên trước những câu dài, và đủ ấm để người nghe muốn bước tiếp vào chương sau."
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
        manifest_presets = _load_manifest_presets(Path(data_root))
        if manifest_presets is not None:
            return cls(presets=manifest_presets)
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


def _load_manifest_presets(data_root: Path) -> tuple[VoicePreset, ...] | None:
    manifest_path = data_root / "models" / "voices" / "voice-presets.manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ()
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "truyenaudio-studio.voice-presets.v1":
        return ()
    raw_presets = manifest.get("presets")
    if not isinstance(raw_presets, list):
        return ()
    presets: list[VoicePreset] = []
    for raw in raw_presets:
        preset = _manifest_preset(data_root, raw)
        if preset is not None:
            presets.append(preset)
    return tuple(presets)


def _manifest_preset(data_root: Path, raw: object) -> VoicePreset | None:
    if not isinstance(raw, dict):
        return None
    try:
        return VoicePreset(
            id=_required_str(raw, "id"),
            name=_required_str(raw, "name"),
            provider=_required_str(raw, "provider"),
            model=_required_str(raw, "model"),
            locale=_required_str(raw, "locale"),
            region=_required_str(raw, "region"),
            gender=_required_str(raw, "gender"),
            license=_required_str(raw, "license"),
            cost_tier=_required_str(raw, "cost_tier"),
            sample_rate=_required_int(raw, "sample_rate"),
            model_path=_manifest_path(data_root, _required_str(raw, "model_path")),
            license_snapshot_path=_manifest_path(data_root, _required_str(raw, "license_snapshot_path")),
            model_sha256=_required_str(raw, "model_sha256"),
            license_snapshot_sha256=_required_str(raw, "license_snapshot_sha256"),
            model_verified=_required_bool(raw, "model_verified"),
            license_verified=_required_bool(raw, "license_verified"),
            poc_passed=_required_bool(raw, "poc_passed"),
            settings_hash=str(raw.get("settings_hash") or "default"),
            pronunciation_hash=str(raw.get("pronunciation_hash") or "default"),
        )
    except (TypeError, ValueError):
        return None


def _manifest_path(data_root: Path, relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("voice manifest paths must stay under data_root")
    candidate = (data_root / path).resolve()
    if not candidate.is_relative_to(data_root.resolve()):
        raise ValueError("voice manifest paths must stay under data_root")
    return candidate


def _required_str(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(key)
    return value


def _required_int(raw: dict[str, object], key: str) -> int:
    value = raw.get(key)
    if type(value) is not int or value <= 0:
        raise ValueError(key)
    return value


def _required_bool(raw: dict[str, object], key: str) -> bool:
    value = raw.get(key)
    if type(value) is not bool:
        raise ValueError(key)
    return value
