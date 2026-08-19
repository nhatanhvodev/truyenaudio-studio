from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.contracts import ArtifactKind
from app.modules.artifacts.store import (
    ArtifactAlreadyExists,
    ArtifactStore,
    ArtifactWrite,
    ArtifactWriterClosed,
    InvalidArtifactDigest,
    StoredArtifact,
    UnsafeArtifactPath,
)


VALID_INPUT_HASH = "a" * 64
VALID_SETTINGS_HASH = "b" * 64


def artifact_write(relative_path: str) -> ArtifactWrite:
    return ArtifactWrite(
        kind=ArtifactKind.REPORT,
        relative_path=relative_path,
        input_hash=VALID_INPUT_HASH,
        settings_hash=VALID_SETTINGS_HASH,
        mime_type="application/json",
    )


def test_commit_is_atomic_verified_and_idempotent(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    payload = b'{"ok":true}'

    with store.begin(artifact_write("projects/p/report.json")) as writer:
        writer.file.write(payload)

        assert writer.partial_path.name.endswith(".partial")
        assert writer.partial_path.exists()
        assert not (tmp_path / "projects/p/report.json").exists()

        saved = writer.commit()
        assert writer.commit() == saved
        writer.close()
        writer.close()

    assert saved == StoredArtifact(
        relative_path="projects/p/report.json",
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
    )
    assert (tmp_path / "projects/p/report.json").read_bytes() == payload
    assert not writer.partial_path.exists()
    assert store.verify(saved.relative_path, saved.sha256)


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        ".",
        "../secret.txt",
        "projects/../../secret.txt",
        "/absolute.txt",
        r"C:\absolute.txt",
    ],
)
def test_rejects_paths_that_are_not_bounded_relative_paths(tmp_path: Path, relative_path: str) -> None:
    with pytest.raises(UnsafeArtifactPath):
        ArtifactStore(tmp_path).resolve(relative_path)


def test_rejects_existing_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(UnsafeArtifactPath):
        ArtifactStore(root).resolve("link/report.json")


def test_concurrent_begins_use_distinct_partial_names(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    writer_a = store.begin(artifact_write("projects/p/report.json"))
    writer_b = store.begin(artifact_write("projects/p/report.json"))
    try:
        assert writer_a.partial_path != writer_b.partial_path
        assert writer_a.partial_path.name.endswith(".partial")
        assert writer_b.partial_path.name.endswith(".partial")
        writer_a.file.write(b"a")
        writer_b.file.write(b"b")
    finally:
        writer_a.close()
        writer_b.close()

    assert list(tmp_path.rglob("*.partial")) == []


def test_uncommitted_context_exit_removes_partial_file(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    with pytest.raises(RuntimeError, match="abort"):
        with store.begin(artifact_write("projects/p/report.json")) as writer:
            writer.file.write(b"partial")
            partial_path = writer.partial_path
            raise RuntimeError("abort")

    assert not partial_path.exists()
    assert list(tmp_path.rglob("*.partial")) == []
    assert not (tmp_path / "projects/p/report.json").exists()


def test_second_commit_to_same_relative_path_cannot_change_original_bytes(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    final_path = tmp_path / "projects/p/report.json"

    with store.begin(artifact_write("projects/p/report.json")) as writer:
        writer.file.write(b"original")
        first = writer.commit()

    with pytest.raises(ArtifactAlreadyExists):
        with store.begin(artifact_write("projects/p/report.json")) as writer:
            writer.file.write(b"replacement")
            writer.commit()

    assert final_path.read_bytes() == b"original"
    assert first.sha256 == hashlib.sha256(b"original").hexdigest()
    assert list(tmp_path.rglob("*.partial")) == []


def test_failed_publish_cleans_partial_without_context_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(tmp_path)
    final_path = tmp_path / "projects/p/report.json"
    writer = store.begin(artifact_write("projects/p/report.json"))
    writer.file.write(b"payload")

    def deny_link(source: Path, target: Path) -> None:
        assert source == writer.partial_path
        assert target == final_path
        raise PermissionError("link denied")

    monkeypatch.setattr("app.modules.artifacts.store.os.link", deny_link)

    with pytest.raises(PermissionError, match="link denied"):
        writer.commit()

    assert not final_path.exists()
    assert list(tmp_path.rglob("*.partial")) == []
    with pytest.raises(ArtifactWriterClosed):
        writer.commit()


def test_verify_returns_false_without_deleting_missing_or_mismatched_artifacts(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    expected_hash = hashlib.sha256(b"expected").hexdigest()
    corrupt_path = tmp_path / "projects/p/report.json"
    corrupt_path.parent.mkdir(parents=True)
    corrupt_path.write_bytes(b"corrupt")

    assert not store.verify("projects/p/missing.json", expected_hash)
    assert not store.verify("projects/p/report.json", expected_hash)
    assert corrupt_path.read_bytes() == b"corrupt"


@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, "g" * 64])
def test_verify_rejects_invalid_expected_digest(tmp_path: Path, digest: str) -> None:
    with pytest.raises(InvalidArtifactDigest):
        ArtifactStore(tmp_path).verify("projects/p/report.json", digest)


def test_commit_after_close_raises_clear_exception(tmp_path: Path) -> None:
    writer = ArtifactStore(tmp_path).begin(artifact_write("projects/p/report.json"))

    writer.close()
    writer.close()

    with pytest.raises(ArtifactWriterClosed):
        writer.commit()
