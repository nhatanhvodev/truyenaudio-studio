"""Release manifest: the machine-readable evidence record that R01 gates rollout on.

The manifest answers one question for every feature that can be switched on:
*is there recorded, reproducible evidence for the behaviour this flag turns on?*

Rules (fail-closed, mirroring app.modules.settings.feature_flags):

- only an evidence state of PASS may switch a flag **on**; FAIL, NOT_RUN and
  BLOCKED keep it off and make an enable request raise
  FEATURE_FLAG_EVIDENCE_NOT_PASS:<flag>;
- a missing manifest is **not** an implicit PASS - every flag that declares an
  evidence_feature stays un-enable-able, so deleting the manifest cannot
  re-open a live feature;
- the manifest is data, not code: load_release_manifest() only reads and
  validates it. Nothing in this module can invent an evidence state.

The default file is docs/validation/release-manifest.json, next to the
release-gate report (docs/validation/release-gate.md); STUDIO_RELEASE_MANIFEST
overrides the path (used by tests and by an out-of-tree release bundle).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1

EVIDENCE_PASS = "PASS"
EVIDENCE_FAIL = "FAIL"
EVIDENCE_NOT_RUN = "NOT_RUN"
EVIDENCE_BLOCKED = "BLOCKED"
EVIDENCE_STATES: tuple[str, ...] = (
    EVIDENCE_PASS,
    EVIDENCE_FAIL,
    EVIDENCE_NOT_RUN,
    EVIDENCE_BLOCKED,
)

RELEASE_MANIFEST_ENV = "STUDIO_RELEASE_MANIFEST"

#: backend/app/modules/settings/release_manifest.py -> repository root.
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MANIFEST_PATH = REPO_ROOT / "docs" / "validation" / "release-manifest.json"


class ReleaseManifestError(RuntimeError):
    """The manifest exists but cannot be trusted (malformed or inconsistent)."""


@dataclass(frozen=True)
class ReleaseFeature:
    """One rollout-relevant feature and the evidence recorded for it.

    flag links the feature to a feature-flag name; None means the feature is
    documented in the release gate but is not switchable through the flag API.
    """

    id: str
    evidence: str
    summary: str
    flag: str | None = None
    evidence_ref: str | None = None

    @property
    def verified(self) -> bool:
        return self.evidence == EVIDENCE_PASS


@dataclass(frozen=True)
class ReleaseManifest:
    """Immutable view of the manifest, including the *unavailable* case."""

    path: Path | None
    available: bool
    schema_version: int
    release_scope: str
    generated_at: str
    commit: str
    features: tuple[ReleaseFeature, ...]
    note: str = ""

    def feature(self, feature_id: str) -> ReleaseFeature | None:
        for feature in self.features:
            if feature.id == feature_id:
                return feature
        return None

    def feature_allows(self, feature_id: str) -> bool:
        """True only when the manifest records PASS for exactly this feature id."""
        feature = self.feature(feature_id)
        return feature is not None and feature.verified

    def features_for_flag(self, flag_name: str) -> tuple[ReleaseFeature, ...]:
        return tuple(feature for feature in self.features if feature.flag == flag_name)

    def flag_allowed(self, flag_name: str) -> bool:
        """True only when the manifest records PASS for every feature of the flag."""
        linked = self.features_for_flag(flag_name)
        return bool(linked) and all(feature.verified for feature in linked)

    def flag_evidence(self, flag_name: str) -> str:
        """Worst evidence state among the flag features, or MISSING."""
        linked = self.features_for_flag(flag_name)
        if not linked:
            return "MISSING"
        for state in (EVIDENCE_FAIL, EVIDENCE_BLOCKED, EVIDENCE_NOT_RUN):
            if any(feature.evidence == state for feature in linked):
                return state
        return EVIDENCE_PASS if all(feature.verified for feature in linked) else "MISSING"

    def not_verified_features(self) -> tuple[ReleaseFeature, ...]:
        return tuple(feature for feature in self.features if not feature.verified)

    def disabled_flags(self, flag_names: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        """Flags that the manifest keeps off (their evidence is not PASS)."""
        return tuple(name for name in flag_names if not self.flag_allowed(name))


def manifest_path() -> Path:
    override = os.environ.get(RELEASE_MANIFEST_ENV)
    if override:
        return Path(override)
    return DEFAULT_MANIFEST_PATH


def unavailable_manifest(path: Path | None = None) -> ReleaseManifest:
    """Fail-closed stand-in used when no manifest file exists."""
    return ReleaseManifest(
        path=path,
        available=False,
        schema_version=SCHEMA_VERSION,
        release_scope="unknown",
        generated_at="",
        commit="",
        features=(),
        note="release manifest missing: every evidence-gated flag stays disabled",
    )


def parse_release_manifest(payload: object, *, path: Path | None = None) -> ReleaseManifest:
    if not isinstance(payload, dict):
        raise ReleaseManifestError("RELEASE_MANIFEST_INVALID:root")

    schema_version = payload.get("schemaVersion")
    if schema_version != SCHEMA_VERSION:
        raise ReleaseManifestError("RELEASE_MANIFEST_INVALID:schemaVersion")

    features_raw = payload.get("features")
    if not isinstance(features_raw, list) or not features_raw:
        raise ReleaseManifestError("RELEASE_MANIFEST_INVALID:features")

    features: list[ReleaseFeature] = []
    seen: set[str] = set()
    for entry in features_raw:
        if not isinstance(entry, dict):
            raise ReleaseManifestError("RELEASE_MANIFEST_INVALID:feature")
        feature = _parse_feature(entry)
        if feature.id in seen:
            raise ReleaseManifestError(f"RELEASE_MANIFEST_INVALID:duplicate:{feature.id}")
        seen.add(feature.id)
        features.append(feature)

    return ReleaseManifest(
        path=path,
        available=True,
        schema_version=SCHEMA_VERSION,
        release_scope=_require_text(payload, "releaseScope"),
        generated_at=_require_text(payload, "generatedAt"),
        commit=_require_text(payload, "commit"),
        features=tuple(features),
        note=str(payload.get("note", "")),
    )


def _parse_feature(entry: dict[str, Any]) -> ReleaseFeature:
    feature_id = _require_text(entry, "id")
    evidence = _require_text(entry, "evidence")
    if evidence not in EVIDENCE_STATES:
        raise ReleaseManifestError(f"RELEASE_MANIFEST_INVALID:evidence:{feature_id}:{evidence}")
    flag = entry.get("flag")
    if flag is not None and not isinstance(flag, str):
        raise ReleaseManifestError(f"RELEASE_MANIFEST_INVALID:flag:{feature_id}")
    evidence_ref = entry.get("evidenceRef")
    if evidence_ref is not None and not isinstance(evidence_ref, str):
        raise ReleaseManifestError(f"RELEASE_MANIFEST_INVALID:evidenceRef:{feature_id}")
    return ReleaseFeature(
        id=feature_id,
        evidence=evidence,
        summary=_require_text(entry, "summary"),
        flag=flag,
        evidence_ref=evidence_ref,
    )


def _require_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReleaseManifestError(f"RELEASE_MANIFEST_INVALID:{key}")
    return value


_manifest_cache: dict[tuple[str, int, int], ReleaseManifest] = {}


def load_release_manifest(path: Path | str | None = None) -> ReleaseManifest:
    """Read and validate the manifest; a missing file maps to unavailable_manifest().

    Malformed content raises ReleaseManifestError on purpose: a broken evidence
    record must fail loudly instead of silently degrading to "no evidence".
    """
    resolved = Path(path) if path is not None else manifest_path()
    try:
        stat = resolved.stat()
    except OSError:
        return unavailable_manifest(resolved)
    key = (str(resolved), stat.st_mtime_ns, stat.st_size)
    cached = _manifest_cache.get(key)
    if cached is not None:
        return cached
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError(f"RELEASE_MANIFEST_INVALID:unreadable:{resolved}") from exc
    manifest = parse_release_manifest(payload, path=resolved)
    _manifest_cache.clear()
    _manifest_cache[key] = manifest
    return manifest


__all__ = [
    "DEFAULT_MANIFEST_PATH",
    "EVIDENCE_BLOCKED",
    "EVIDENCE_FAIL",
    "EVIDENCE_NOT_RUN",
    "EVIDENCE_PASS",
    "EVIDENCE_STATES",
    "RELEASE_MANIFEST_ENV",
    "SCHEMA_VERSION",
    "ReleaseFeature",
    "ReleaseManifest",
    "ReleaseManifestError",
    "load_release_manifest",
    "manifest_path",
    "parse_release_manifest",
    "unavailable_manifest",
]
