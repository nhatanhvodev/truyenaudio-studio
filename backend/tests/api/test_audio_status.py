from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.audio import create_audio_router
from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ChapterState,
    RightsStatus,
    SourceType,
)
from app.db.models import Artifact, Chapter, Project
from app.main import create_app
from app.settings.config import Settings
from tests.speech.test_single_narrator import _approved_chapter, _voice_preset


LOOPBACK_ORIGIN = "http://127.0.0.1:8765"


def test_audio_status_loads_latest_pending_master_from_database(
    settings: Settings, db_session
) -> None:
    project = Project(
        id="018f0000-0000-7000-8000-000000000501",
        title="Audio",
        slug="audio",
        source_type=SourceType.SELF_AUTHORED.value,
        rights_status=RightsStatus.CLEARED.value,
    )
    chapter = Chapter(
        id="018f0000-0000-7000-8000-000000000502",
        project_id=project.id,
        ordinal=1,
        state=ChapterState.AUDIO_REVIEW.value,
    )
    older = Artifact(
        id="018f0000-0000-7000-8000-000000000601",
        chapter_id=chapter.id,
        kind=ArtifactKind.MASTER_MP3.value,
        status=ArtifactStatus.READY.value,
        relative_path="older.mp3",
        sha256="a" * 64,
        byte_size=128,
        mime_type="audio/mpeg",
        input_hash="b" * 64,
        settings_hash="c" * 64,
        updated_at=datetime(2026, 8, 21, 3, 0, tzinfo=UTC),
    )
    latest = Artifact(
        id="018f0000-0000-7000-8000-000000000602",
        chapter_id=chapter.id,
        kind=ArtifactKind.MASTER_MP3.value,
        status=ArtifactStatus.READY.value,
        relative_path="latest.mp3",
        sha256="d" * 64,
        byte_size=256,
        mime_type="audio/mpeg",
        input_hash="e" * 64,
        settings_hash="f" * 64,
        updated_at=datetime(2026, 8, 21, 3, 1, tzinfo=UTC),
    )
    db_session.add(project)
    db_session.flush()
    db_session.add(chapter)
    db_session.flush()
    db_session.add_all([older, latest])
    db_session.commit()

    client = TestClient(
        create_app(settings=settings, acquire_lock=False), base_url=LOOPBACK_ORIGIN
    )
    payload = client.get(f"/api/chapters/{chapter.id}/audio/status").json()

    assert payload == {
        "chapterId": chapter.id,
        "masterArtifactId": latest.id,
        "masterSha256": "d" * 64,
        "approved": False,
    }


def test_audio_render_route_refuses_implicit_fake_tts(
    settings: Settings, db_session
) -> None:
    from fastapi import FastAPI

    fixture = _approved_chapter(db_session)
    preset = _voice_preset(db_session)
    app = FastAPI()
    app.include_router(create_audio_router(settings))
    client = TestClient(app, base_url=LOOPBACK_ORIGIN)

    configured = client.post(
        f"/api/chapters/{fixture.chapter_id}/audio/configure-single",
        json={"presetId": preset.id},
    )
    rendered = client.post(f"/api/chapters/{fixture.chapter_id}/audio/render", json={})

    assert configured.status_code == 200
    assert rendered.status_code == 409
    assert rendered.json()["detail"] == "LOCAL_TTS_ADAPTER_REQUIRED"
