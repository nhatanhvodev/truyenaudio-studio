"""A03 - async render enqueue route, kept next to the inline render route.

The frontend keeps calling POST /api/chapters/{id}/audio/render (still synchronous and
still answering a RenderedChapterView); POST .../audio/render-jobs adds the durable
worker path for long chapters. These tests pin both behaviours without any network call.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.audio import create_audio_router
from app.contracts import ArtifactKind, ArtifactStatus, ChapterState, JobKind, JobStatus, RunStatus
from app.db.models import Artifact, Chapter
from app.settings.config import Settings
from tests.speech.test_render_resumable import (
    _chapter_with_translation,
    _configure_plan,
    _voice_preset,
)


LOOPBACK_ORIGIN = "http://127.0.0.1:8765"


def _client(settings: Settings) -> TestClient:
    app = FastAPI()
    app.include_router(create_audio_router(settings))
    return TestClient(app, base_url=LOOPBACK_ORIGIN)


def _job_row(engine: Engine, job_id: str) -> dict[str, object]:
    from sqlalchemy import text

    with engine.connect() as connection:
        return dict(
            connection.execute(
                text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}
            ).mappings().one()
        )


def test_render_jobs_route_enqueues_one_durable_synthesize_job(
    settings: Settings, migrated_engine: Engine, db_session
) -> None:
    import json

    fixture = _chapter_with_translation(db_session, segment_count=3)
    preset = _voice_preset(db_session)
    plan = _configure_plan(db_session, fixture, preset.id)
    client = _client(settings)

    first = client.post(
        f"/api/chapters/{fixture.chapter_id}/audio/render-jobs", json={}
    )
    payload = first.json()

    assert first.status_code == 200
    assert payload["kind"] == JobKind.SYNTHESIZE.value
    assert payload["status"] == JobStatus.QUEUED.value
    assert payload["deduplicated"] is False

    row = _job_row(migrated_engine, payload["jobId"])
    assert row["chapter_id"] == fixture.chapter_id
    assert row["project_id"] == fixture.project_id
    plan_payload = json.loads(row["plan_json"])
    assert plan_payload["translationRunId"] == fixture.run_id
    assert plan_payload["voicePlanId"] == plan.id
    assert plan_payload["revisionId"] == fixture.revision_id
    assert plan_payload["projectId"] == fixture.project_id
    assert plan_payload["profileId"] == "local-tts"

    second = client.post(
        f"/api/chapters/{fixture.chapter_id}/audio/render-jobs",
        json={"idempotencyKey": "another-click"},
    ).json()

    assert second["deduplicated"] is True
    assert second["jobId"] == payload["jobId"]


def test_render_jobs_route_refuses_chapter_without_approved_translation(
    settings: Settings, db_session
) -> None:
    fixture = _chapter_with_translation(
        db_session, segment_count=3, run_status=RunStatus.REVIEW
    )
    _voice_preset(db_session)
    client = _client(settings)

    response = client.post(
        f"/api/chapters/{fixture.chapter_id}/audio/render-jobs", json={}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "TRANSLATION_APPROVAL_REQUIRED"


def test_render_jobs_route_requires_a_voice_plan(
    settings: Settings, db_session
) -> None:
    fixture = _chapter_with_translation(db_session, segment_count=3)
    client = _client(settings)

    response = client.post(
        f"/api/chapters/{fixture.chapter_id}/audio/render-jobs", json={}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "VOICE_PLAN_REQUIRED"


def test_render_jobs_route_reports_unknown_chapter(
    settings: Settings, db_session
) -> None:
    client = _client(settings)

    response = client.post(
        "/api/chapters/018f0000-0000-7002-8000-00000000ffff/audio/render-jobs",
        json={},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "CHAPTER_NOT_FOUND"


def test_inline_render_route_still_answers_a_rendered_chapter_view(
    settings: Settings, db_session, monkeypatch
) -> None:
    fixture = _chapter_with_translation(db_session, segment_count=3)
    preset = _voice_preset(db_session)
    monkeypatch.setenv("STUDIO_FAKE_AUDIO", "1")
    client = _client(settings)

    configured = client.post(
        f"/api/chapters/{fixture.chapter_id}/audio/configure-single",
        json={"presetId": preset.id},
    )
    rendered = client.post(
        f"/api/chapters/{fixture.chapter_id}/audio/render", json={}
    )

    assert configured.status_code == 200
    assert rendered.status_code == 200
    payload = rendered.json()
    assert len(payload["segmentIds"]) == 3
    assert payload["renderedSegmentIds"] == payload["segmentIds"]
    assert payload["reusedSegmentIds"] == []
    assert payload["masterArtifactId"]
    assert payload["srtArtifactId"]
    assert payload["masterSha256"]

    db_session.expire_all()
    artifacts = (
        db_session.query(Artifact)
        .filter(
            Artifact.chapter_id == fixture.chapter_id,
            Artifact.kind == ArtifactKind.TTS_SEGMENT.value,
            Artifact.status == ArtifactStatus.READY.value,
        )
        .all()
    )
    assert len(artifacts) == 3
    chapter = db_session.get(Chapter, fixture.chapter_id)
    assert chapter.state == ChapterState.AUDIO_REVIEW.value
    for artifact in artifacts:
        assert (Path(settings.data_root) / "artifacts" / artifact.relative_path).is_file()
