"""A05 — artifact Range/HEAD serving, path confinement and the audio approval guard.

Real FastAPI routes, a real alembic-migrated SQLite database and real artifact bytes on disk.
The A05 acceptance is "200/206/416 đúng; không Blob toàn master; stale không approve", so these
tests assert exact HTTP status codes, byte-exact range bodies, ETag/If-None-Match/If-Range
handling, that a path outside the artifact root is never served, and that the approval guard
refuses a stale or blocked master instead of approving it.

No network, no cloud call and no TTS model is involved. The router's documented local
STUDIO_FAKE_AUDIO switch selects FakeMp3AudioProcessor for the approval probe, which is the same
guard path a real master walks. Fake playback proves the guard wiring only; it does NOT prove
VieNeu audio quality (that stays NOT_RUN, see the task report).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import hashlib
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.audio import create_audio_router
from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ChapterState,
    ImportKind,
    QaCategory,
    QaSeverity,
    QaStatus,
    RunStatus,
    SourceType,
    VoiceMode,
    VoiceOrigin,
)
from app.db.base import create_engine_for
from app.db.models import (
    Artifact,
    Chapter,
    Project,
    QaIssue,
    SourceRevision,
    TranslationRun,
    VoicePlan,
    VoicePreset,
)
from app.settings.config import Settings


MASTER_BYTES = bytes(range(256)) * 4  # 1024 deterministic bytes
TOTAL = len(MASTER_BYTES)
ESCAPE_BYTES = b"outside the artifact root"

PROJECT_ID = "018f0000-0000-7000-8000-00000000a001"
CHAPTER_ID = "018f0000-0000-7000-8000-00000000c001"
OTHER_CHAPTER_ID = "018f0000-0000-7000-8000-00000000c002"
REVISION_ID = "018f0000-0000-7000-8000-00000000b001"
RUN_ID = "018f0000-0000-7000-8000-00000000d001"
PRESET_ID = "018f0000-0000-7000-8000-00000000e001"
PLAN_ID = "018f0000-0000-7000-8000-00000000e002"
MASTER_ID = "018f0000-0000-7000-8000-00000000f001"
STALE_ID = "018f0000-0000-7000-8000-00000000f002"
QA_ISSUE_ID = "018f0000-0000-7000-8000-00000000a002"
MASTER_PATH = "audio/chapter/masters/master.mp3"


@dataclass(frozen=True)
class Seed:
    chapter_id: str
    other_chapter_id: str
    run_id: str
    voice_plan_id: str


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def engine(data_root: Path) -> Iterator[Engine]:
    data_root.mkdir(parents=True, exist_ok=True)
    db_path = data_root / "studio.sqlite3"
    engine = create_engine_for(db_path)
    backend_root = Path(__file__).parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    command.upgrade(config, "head")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with _factory(engine)() as session:
        yield session


@pytest.fixture
def fresh_session(engine: Engine) -> Iterator[Session]:
    """A second connection, used to verify what the API committed."""
    with _factory(engine)() as session:
        yield session


@pytest.fixture
def seed(session: Session) -> Seed:
    """Committed project/chapter/run/voice-plan rows the API's own connection can see."""
    project = Project(
        id=PROJECT_ID,
        title="Truyen",
        slug="truyen-a05",
        source_type=SourceType.SELF_AUTHORED.value,
        rights_status="CLEARED",
        target_language="vi-VN",
    )
    chapter = Chapter(
        id=CHAPTER_ID,
        project_id=PROJECT_ID,
        ordinal=1,
        source_title="Mot",
        translated_title="Mot",
        state=ChapterState.AUDIO_REVIEW.value,
    )
    other_chapter = Chapter(
        id=OTHER_CHAPTER_ID,
        project_id=PROJECT_ID,
        ordinal=2,
        source_title="Hai",
        translated_title="Hai",
        state=ChapterState.AUDIO_REVIEW.value,
    )
    revision = SourceRevision(
        id=REVISION_ID,
        chapter_id=CHAPTER_ID,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text="source text",
        normalized_sha256=_sha(b"source text"),
        han_char_count=0,
        total_char_count=11,
        normalizer_version="nfc-v1",
    )
    run = TranslationRun(
        id=RUN_ID,
        chapter_id=CHAPTER_ID,
        source_revision_id=REVISION_ID,
        prompt_version="translation-v1",
        status=RunStatus.APPROVED.value,
        translation_text_sha256=_sha(b"Ban dich."),
    )
    preset = VoicePreset(
        id=PRESET_ID,
        name="Narrator",
        provider_voice_id="fake-vi-narrator",
        locale="vi-VN",
        origin=VoiceOrigin.BUILT_IN.value,
        speed="1.0",
        pitch="0",
        sample_rate=44_100,
        settings_json={"speed": "1.0", "pitch": "0"},
        model_snapshot_hash=_sha(b"model"),
        active=True,
    )
    plan = VoicePlan(
        id=PLAN_ID,
        chapter_id=CHAPTER_ID,
        revision_no=1,
        mode=VoiceMode.SINGLE_NARRATOR.value,
        narrator_preset_id=PRESET_ID,
        plan_sha256=_sha(b"plan"),
    )
    session.add(project)
    session.flush()
    session.add_all((chapter, other_chapter))
    session.flush()
    session.add_all((revision, run))
    session.flush()
    session.add(preset)
    session.flush()
    session.add(plan)
    session.flush()
    chapter.active_source_revision_id = REVISION_ID
    chapter.approved_translation_run_id = RUN_ID
    chapter.active_voice_plan_id = PLAN_ID
    session.commit()
    return Seed(
        chapter_id=CHAPTER_ID,
        other_chapter_id=OTHER_CHAPTER_ID,
        run_id=RUN_ID,
        voice_plan_id=PLAN_ID,
    )


@pytest.fixture
def client(data_root: Path, seed: Seed, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # The router's documented local fake-audio switch: the approval probe runs through
    # FakeMp3AudioProcessor instead of real ffmpeg. Nothing is downloaded and no network call
    # is possible (tests/conftest.py blocks non-loopback sockets too).
    monkeypatch.setenv("STUDIO_FAKE_AUDIO", "1")
    app = FastAPI()
    app.include_router(create_audio_router(Settings(data_root=data_root)))
    with TestClient(app) as test_client:
        yield test_client


def _factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _content_url(chapter_id: str, artifact_id: str) -> str:
    return f"/api/chapters/{chapter_id}/audio/artifacts/{artifact_id}/content"


def _add_artifact(
    session: Session,
    data_root: Path,
    *,
    artifact_id: str = MASTER_ID,
    chapter_id: str = CHAPTER_ID,
    relative_path: str = MASTER_PATH,
    payload: bytes = MASTER_BYTES,
    status: str = ArtifactStatus.READY.value,
    kind: str = ArtifactKind.MASTER_MP3.value,
    mime_type: str = "audio/mpeg",
    write_file: bool = True,
    seed: Seed | None = None,
) -> Artifact:
    if write_file:
        path = data_root / "artifacts" / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    metadata: dict[str, str] = {"codec": "mp3"}
    if seed is not None:
        metadata["translation_run_id"] = seed.run_id
        metadata["voice_plan_id"] = seed.voice_plan_id
    artifact = Artifact(
        id=artifact_id,
        chapter_id=chapter_id,
        kind=kind,
        status=status,
        relative_path=relative_path,
        sha256=_sha(payload),
        byte_size=len(payload),
        mime_type=mime_type,
        duration_ms=61_000,
        input_hash=_sha(f"input:{artifact_id}".encode()),
        settings_hash=_sha(f"settings:{artifact_id}".encode()),
        metadata_json=metadata,
    )
    session.add(artifact)
    session.commit()
    return artifact


def _add_open_qa_issue(
    session: Session,
    *,
    severity: QaSeverity,
    category: QaCategory = QaCategory.CLIPPING,
    status: QaStatus = QaStatus.OPEN,
    issue_id: str = QA_ISSUE_ID,
) -> None:
    session.add(
        QaIssue(
            id=issue_id,
            chapter_id=CHAPTER_ID,
            category=category.value,
            severity=severity.value,
            status=status.value,
            evidence="segment peak -0.2 dBFS",
            suggestion="Regenerate or lower gain.",
            rule_or_model="audio-qa-v1",
        )
    )
    session.commit()


def test_full_get_returns_whole_file_with_immutable_cache_headers(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed)

    response = client.get(_content_url(CHAPTER_ID, MASTER_ID))

    assert response.status_code == 200
    assert response.content == MASTER_BYTES
    assert response.headers["content-length"] == str(TOTAL)
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["etag"] == f'"{_sha(MASTER_BYTES)}"'
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert "content-range" not in response.headers


def test_head_returns_same_headers_without_body(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed)

    response = client.head(_content_url(CHAPTER_ID, MASTER_ID))

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == str(TOTAL)
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["etag"] == f'"{_sha(MASTER_BYTES)}"'


@pytest.mark.parametrize(
    ("range_header", "expected_body", "expected_content_range"),
    [
        pytest.param("bytes=0-99", MASTER_BYTES[0:100], f"bytes 0-99/{TOTAL}", id="prefix"),
        pytest.param("bytes=100-199", MASTER_BYTES[100:200], f"bytes 100-199/{TOTAL}", id="middle"),
        pytest.param("bytes=1000-", MASTER_BYTES[1000:], f"bytes 1000-1023/{TOTAL}", id="open-ended"),
        pytest.param("bytes=-16", MASTER_BYTES[-16:], f"bytes 1008-1023/{TOTAL}", id="suffix"),
        pytest.param("bytes=-99999", MASTER_BYTES, f"bytes 0-1023/{TOTAL}", id="suffix-larger-than-file"),
        pytest.param("bytes=1000-5000", MASTER_BYTES[1000:], f"bytes 1000-1023/{TOTAL}", id="end-past-eof"),
        pytest.param("bytes=1023-1023", MASTER_BYTES[1023:], f"bytes 1023-1023/{TOTAL}", id="last-byte"),
        pytest.param("bytes=0-0", MASTER_BYTES[0:1], f"bytes 0-0/{TOTAL}", id="first-byte"),
        pytest.param("BYTES=0-9", MASTER_BYTES[0:10], f"bytes 0-9/{TOTAL}", id="unit-case-insensitive"),
    ],
)
def test_range_requests_return_byte_exact_206(
    client: TestClient,
    session: Session,
    data_root: Path,
    seed: Seed,
    range_header: str,
    expected_body: bytes,
    expected_content_range: str,
) -> None:
    _add_artifact(session, data_root, seed=seed)

    response = client.get(_content_url(CHAPTER_ID, MASTER_ID), headers={"Range": range_header})

    assert response.status_code == 206
    assert response.content == expected_body
    assert response.headers["content-length"] == str(len(expected_body))
    assert response.headers["content-range"] == expected_content_range
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["etag"] == f'"{_sha(MASTER_BYTES)}"'


def test_head_with_range_returns_206_headers_without_body(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed)

    response = client.head(
        _content_url(CHAPTER_ID, MASTER_ID), headers={"Range": "bytes=10-19"}
    )

    assert response.status_code == 206
    assert response.content == b""
    assert response.headers["content-length"] == "10"
    assert response.headers["content-range"] == f"bytes 10-19/{TOTAL}"


@pytest.mark.parametrize(
    "range_header",
    [
        pytest.param(f"bytes={TOTAL}-", id="start-at-eof"),
        pytest.param(f"bytes={TOTAL + 10}-{TOTAL + 20}", id="start-past-eof"),
        pytest.param("bytes=500-100", id="end-before-start"),
        pytest.param("bytes=abc-def", id="not-numeric"),
        pytest.param("bytes=", id="empty-spec"),
        pytest.param("bytes=-0", id="zero-suffix"),
        pytest.param("bytes=-", id="no-bounds"),
        pytest.param("items=0-1", id="wrong-unit"),
        pytest.param("0-1", id="missing-unit"),
    ],
)
def test_unsatisfiable_or_malformed_range_returns_416(
    client: TestClient, session: Session, data_root: Path, seed: Seed, range_header: str
) -> None:
    _add_artifact(session, data_root, seed=seed)

    response = client.get(_content_url(CHAPTER_ID, MASTER_ID), headers={"Range": range_header})

    assert response.status_code == 416
    assert response.content == b""
    assert response.headers["content-range"] == f"bytes */{TOTAL}"
    assert response.headers["accept-ranges"] == "bytes"


def test_multi_range_returns_whole_file_instead_of_multipart(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    """Deliberate A05 decision: multipart/byteranges is not supported, so 200 + full body."""
    _add_artifact(session, data_root, seed=seed)

    response = client.get(
        _content_url(CHAPTER_ID, MASTER_ID), headers={"Range": "bytes=0-1,4-5"}
    )

    assert response.status_code == 200
    assert response.content == MASTER_BYTES
    assert response.headers["content-length"] == str(TOTAL)
    assert response.headers["content-type"] == "audio/mpeg"
    assert "content-range" not in response.headers


def test_if_none_match_returns_304_without_body(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed)
    etag = f'"{_sha(MASTER_BYTES)}"'

    response = client.get(_content_url(CHAPTER_ID, MASTER_ID), headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag


def test_head_if_none_match_returns_304(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed)
    etag = f'"{_sha(MASTER_BYTES)}"'

    response = client.head(_content_url(CHAPTER_ID, MASTER_ID), headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.content == b""


def test_if_none_match_stale_validator_still_returns_body(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed)

    response = client.get(
        _content_url(CHAPTER_ID, MASTER_ID), headers={"If-None-Match": '"' + "0" * 64 + '"'}
    )

    assert response.status_code == 200
    assert response.content == MASTER_BYTES


def test_if_range_mismatch_ignores_range_and_returns_whole_file(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed)

    response = client.get(
        _content_url(CHAPTER_ID, MASTER_ID),
        headers={"Range": "bytes=0-9", "If-Range": '"' + "0" * 64 + '"'},
    )

    assert response.status_code == 200
    assert response.content == MASTER_BYTES
    assert "content-range" not in response.headers


def test_if_range_match_still_serves_the_partial_range(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed)

    response = client.get(
        _content_url(CHAPTER_ID, MASTER_ID),
        headers={"Range": "bytes=0-9", "If-Range": f'"{_sha(MASTER_BYTES)}"'},
    )

    assert response.status_code == 206
    assert response.content == MASTER_BYTES[0:10]


def test_unknown_artifact_id_returns_404(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed)

    response = client.get(_content_url(CHAPTER_ID, "018f0000-0000-7000-8000-00000000ffff"))

    assert response.status_code == 404
    assert response.json()["detail"] == "ARTIFACT_NOT_FOUND"


def test_artifact_from_another_chapter_returns_404(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed)

    response = client.get(_content_url(OTHER_CHAPTER_ID, MASTER_ID))

    assert response.status_code == 404


def test_artifact_that_is_not_ready_returns_404(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(
        session, data_root, seed=seed, status=ArtifactStatus.SUPERSEDED.value
    )

    response = client.get(_content_url(CHAPTER_ID, MASTER_ID))

    assert response.status_code == 404


def test_artifact_row_without_file_on_disk_returns_404_not_500(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed, write_file=False)

    response = client.get(_content_url(CHAPTER_ID, MASTER_ID))

    assert response.status_code == 404
    assert response.json()["detail"] == "ARTIFACT_NOT_FOUND"


def test_relative_path_traversal_is_confined_and_never_served(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    (data_root / "artifacts").mkdir(parents=True, exist_ok=True)
    escape_path = data_root / "escape.bin"
    escape_path.write_bytes(ESCAPE_BYTES)
    _add_artifact(
        session,
        data_root,
        seed=seed,
        relative_path="../escape.bin",
        payload=ESCAPE_BYTES,
        write_file=False,
    )

    response = client.get(_content_url(CHAPTER_ID, MASTER_ID))

    assert response.status_code == 404
    assert ESCAPE_BYTES not in response.content
    assert escape_path.read_bytes() == ESCAPE_BYTES


def test_absolute_relative_path_is_confined_and_never_served(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(
        session,
        data_root,
        seed=seed,
        relative_path="C:/Windows/win.ini",
        payload=ESCAPE_BYTES,
        write_file=False,
    )

    response = client.get(_content_url(CHAPTER_ID, MASTER_ID))

    assert response.status_code == 404


def test_status_reports_latest_ready_master_and_not_approved(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed)

    payload = client.get(f"/api/chapters/{CHAPTER_ID}/audio/status").json()

    assert payload == {
        "chapterId": CHAPTER_ID,
        "masterArtifactId": MASTER_ID,
        "masterSha256": _sha(MASTER_BYTES),
        "approved": False,
    }


def test_approve_with_stale_expected_hash_returns_409_and_does_not_approve(
    client: TestClient, session: Session, data_root: Path, seed: Seed, fresh_session: Session
) -> None:
    _add_artifact(session, data_root, seed=seed)

    response = client.post(
        f"/api/chapters/{CHAPTER_ID}/audio/approve",
        json={"masterArtifactId": MASTER_ID, "expectedSha256": "0" * 64},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "MASTER_HASH_MISMATCH"
    assert fresh_session.get(Chapter, CHAPTER_ID).approved_master_artifact_id is None


def test_approve_rejects_master_from_a_stale_translation_run(
    client: TestClient, session: Session, data_root: Path, seed: Seed, fresh_session: Session
) -> None:
    _add_artifact(session, data_root, seed=seed)
    next_run = TranslationRun(
        id="018f0000-0000-7000-8000-00000000d002",
        chapter_id=CHAPTER_ID,
        source_revision_id=REVISION_ID,
        prompt_version="translation-v1",
        status=RunStatus.APPROVED.value,
        translation_text_sha256=_sha(b"Ban dich moi."),
    )
    session.add(next_run)
    session.flush()
    chapter = session.get(Chapter, CHAPTER_ID)
    assert chapter is not None
    chapter.approved_translation_run_id = next_run.id
    session.commit()

    response = client.post(
        f"/api/chapters/{CHAPTER_ID}/audio/approve",
        json={"masterArtifactId": MASTER_ID, "expectedSha256": _sha(MASTER_BYTES)},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "MASTER_TRANSLATION_STALE"
    assert fresh_session.get(Chapter, CHAPTER_ID).approved_master_artifact_id is None


def test_approve_is_blocked_by_open_major_qa_issue(
    client: TestClient, session: Session, data_root: Path, seed: Seed, fresh_session: Session
) -> None:
    _add_artifact(session, data_root, seed=seed)
    _add_open_qa_issue(session, severity=QaSeverity.MAJOR)

    response = client.post(
        f"/api/chapters/{CHAPTER_ID}/audio/approve",
        json={"masterArtifactId": MASTER_ID, "expectedSha256": _sha(MASTER_BYTES)},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "AUDIO_QA_BLOCKERS_OPEN"
    assert fresh_session.get(Chapter, CHAPTER_ID).approved_master_artifact_id is None


def test_approve_is_blocked_by_open_critical_qa_issue(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed)
    _add_open_qa_issue(session, severity=QaSeverity.CRITICAL, category=QaCategory.AUDIO_TECHNICAL)

    response = client.post(
        f"/api/chapters/{CHAPTER_ID}/audio/approve",
        json={"masterArtifactId": MASTER_ID, "expectedSha256": _sha(MASTER_BYTES)},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "AUDIO_QA_BLOCKERS_OPEN"


def test_approve_ignores_minor_translation_qa_issue(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    """Only MAJOR/CRITICAL audio issues block; a MINOR one must not."""
    _add_artifact(session, data_root, seed=seed)
    _add_open_qa_issue(session, severity=QaSeverity.MINOR, category=QaCategory.RESIDUAL_HAN)

    response = client.post(
        f"/api/chapters/{CHAPTER_ID}/audio/approve",
        json={"masterArtifactId": MASTER_ID, "expectedSha256": _sha(MASTER_BYTES)},
    )

    assert response.status_code == 200


def test_approve_unknown_artifact_returns_404(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed)

    response = client.post(
        f"/api/chapters/{CHAPTER_ID}/audio/approve",
        json={
            "masterArtifactId": "018f0000-0000-7000-8000-00000000ffff",
            "expectedSha256": _sha(MASTER_BYTES),
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "MASTER_ARTIFACT_NOT_FOUND"


def test_approve_artifact_of_another_chapter_returns_404(
    client: TestClient, session: Session, data_root: Path, seed: Seed
) -> None:
    _add_artifact(session, data_root, seed=seed, chapter_id=OTHER_CHAPTER_ID)

    response = client.post(
        f"/api/chapters/{CHAPTER_ID}/audio/approve",
        json={"masterArtifactId": MASTER_ID, "expectedSha256": _sha(MASTER_BYTES)},
    )

    assert response.status_code == 404


def test_approve_valid_master_sets_chapter_approval_and_status(
    client: TestClient, session: Session, data_root: Path, seed: Seed, fresh_session: Session
) -> None:
    _add_artifact(session, data_root, seed=seed)

    response = client.post(
        f"/api/chapters/{CHAPTER_ID}/audio/approve",
        json={"masterArtifactId": MASTER_ID, "expectedSha256": _sha(MASTER_BYTES)},
    )

    assert response.status_code == 200
    assert response.json() == {
        "chapterId": CHAPTER_ID,
        "masterArtifactId": MASTER_ID,
        "status": "APPROVED",
    }
    chapter = fresh_session.get(Chapter, CHAPTER_ID)
    assert chapter is not None
    fresh_session.refresh(chapter)
    assert chapter.approved_master_artifact_id == MASTER_ID
    assert chapter.audio_approved_at is not None
    assert chapter.state == ChapterState.READY_TO_EXPORT.value

    status = client.get(f"/api/chapters/{CHAPTER_ID}/audio/status").json()
    assert status == {
        "chapterId": CHAPTER_ID,
        "masterArtifactId": MASTER_ID,
        "masterSha256": _sha(MASTER_BYTES),
        "approved": True,
    }
