from __future__ import annotations

import hashlib

from app.contracts import ArtifactKind, ArtifactStatus
from app.db.models import Artifact, AuditEvent
from app.modules.artifacts.cache import ArtifactCache, canonical_sha256


def test_lookup_returns_ready_artifact_after_size_and_sha256_verification(db_session, artifact_store) -> None:
    ready = _insert_ready_artifact(db_session, artifact_store, b"verified audio")
    cache = ArtifactCache(db_session, artifact_store.resolve())

    hit = cache.lookup(ArtifactKind.TTS_SEGMENT, ready.input_hash, ready.settings_hash)

    assert hit is not None
    assert hit.id == ready.id
    assert hit.status == ArtifactStatus.READY.value


def test_missing_file_is_not_cache_hit(db_session, artifact_store) -> None:
    ready = _insert_ready_artifact(db_session, artifact_store, b"verified audio")
    artifact_store.resolve(ready.relative_path).unlink()
    cache = ArtifactCache(db_session, artifact_store.resolve())

    assert cache.lookup(ready.kind, ready.input_hash, ready.settings_hash) is None

    db_session.refresh(ready)
    assert ready.status == ArtifactStatus.CORRUPT.value
    assert _audit_actions(db_session) == ("ARTIFACT_CACHE_CORRUPT",)


def test_size_or_sha256_mismatch_is_not_cache_hit(db_session, artifact_store) -> None:
    ready = _insert_ready_artifact(db_session, artifact_store, b"verified audio")
    artifact_store.resolve(ready.relative_path).write_bytes(b"tampered")
    cache = ArtifactCache(db_session, artifact_store.resolve())

    assert cache.lookup(ArtifactKind.TTS_SEGMENT, ready.input_hash, ready.settings_hash) is None

    db_session.refresh(ready)
    assert ready.status == ArtifactStatus.CORRUPT.value
    assert _audit_actions(db_session) == ("ARTIFACT_CACHE_CORRUPT",)


def test_non_ready_artifact_is_not_cache_hit_or_marked_corrupt(db_session, artifact_store) -> None:
    ready = _insert_ready_artifact(db_session, artifact_store, b"verified audio")
    ready.status = ArtifactStatus.SUPERSEDED.value
    db_session.commit()
    cache = ArtifactCache(db_session, artifact_store.resolve())

    assert cache.lookup(ArtifactKind.TTS_SEGMENT, ready.input_hash, ready.settings_hash) is None

    db_session.refresh(ready)
    assert ready.status == ArtifactStatus.SUPERSEDED.value
    assert _audit_actions(db_session) == ()


def test_canonical_sha256_has_golden_vector() -> None:
    assert (
        canonical_sha256({"b": 2, "a": ["meo", {"z": None, "flag": True}]})
        == "dadf6568a7fa718ac75907b4802768d9ad9dca353a3f7565bc6e4b4fb0a4be22"
    )


def _insert_ready_artifact(db_session, artifact_store, payload: bytes) -> Artifact:
    artifact_id = "018f0000-0000-7000-8000-000000000101"
    relative_path = f"cache/{artifact_id}.wav"
    path = artifact_store.resolve(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    artifact = Artifact(
        id=artifact_id,
        kind=ArtifactKind.TTS_SEGMENT.value,
        status=ArtifactStatus.READY.value,
        relative_path=relative_path,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        mime_type="audio/wav",
        input_hash=_sha("input"),
        settings_hash=_sha("settings"),
    )
    db_session.add(artifact)
    db_session.commit()
    return artifact


def _audit_actions(db_session) -> tuple[str, ...]:
    return tuple(event.action for event in db_session.query(AuditEvent).order_by(AuditEvent.created_at, AuditEvent.id))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
