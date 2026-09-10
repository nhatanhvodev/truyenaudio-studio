from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import socket
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ChapterState,
    ImportKind,
    MasterResult,
    RightsScope,
    RightsStatus,
    RunStatus,
    SourceType,
)
from app.db.models import (
    Artifact,
    Chapter,
    Export,
    Project,
    RightsEvidence,
    RightsGrant,
    SourceRevision,
    SourceSegment,
    SpeechSegment,
    TranslationRun,
    TranslationSegment,
    VoicePlan,
    VoicePreset,
    VoiceRole,
)
from app.main import create_app
from app.modules.exports.schemas import PublicationMetadata
from app.modules.exports.workflow import ExportWorkflow


NOW = datetime(2026, 8, 21, 0, 0, tzinfo=UTC)
EXPECTED_PUBLICATION_FILES = {
    "tap-0001.mp3",
    "metadata.json",
    "ban-dich.md",
    "transcript.srt",
    "production-report.json",
    "provenance.json",
    "THIRD_PARTY_LICENSES.txt",
    "checksums.sha256",
}


@pytest.fixture
def workflow(db_session, artifact_store) -> ExportWorkflow:
    return ExportWorkflow(
        db_session,
        artifact_root=artifact_store.resolve(),
        audio_processor=DeterministicProbe(),
        now=lambda: NOW,
    )


@pytest.fixture
def cleared_chapter(db_session, artifact_store) -> Chapter:
    return _ready_chapter(db_session, artifact_store.resolve(), key="cleared")


@pytest.fixture
def publication(workflow: ExportWorkflow, cleared_chapter: Chapter):
    return workflow.build_publication_bundle(
        cleared_chapter.id, PublicationMetadata("Tap mot", 1, False)
    )


def test_status_returns_gate_and_verified_publication_bundle(
    settings, cleared_chapter: Chapter, publication
) -> None:
    body = _status(settings, cleared_chapter.id)

    assert body["chapterId"] == cleared_chapter.id
    assert body["gate"]["allowed"] is True
    assert body["gate"]["reasons"] == []
    assert len(body["gate"]["rightsEvaluationHash"]) == 64
    assert body["gate"]["rightsEvaluationHash"] == publication_rights_hash(publication)

    assert [bundle["kind"] for bundle in body["bundles"]] == ["PUBLICATION_BUNDLE"]
    bundle = body["bundles"][0]
    assert bundle["id"] == publication.id
    assert bundle["status"] == "READY"
    assert bundle["manifestSha256"] == publication.manifest_sha256
    assert bundle["artifactId"] == publication.artifact_id
    assert bundle["verified"] is True
    assert bundle["mismatches"] == []
    assert bundle["stale"] is False
    assert bundle["staleReasons"] == []
    assert datetime.fromisoformat(bundle["createdAt"]).tzinfo is not None
    assert Path(bundle["directoryPath"]).name == publication.id


def test_status_lists_the_files_that_really_live_in_the_bundle_zip(
    settings, cleared_chapter: Chapter, publication
) -> None:
    body = _status(settings, cleared_chapter.id)

    with zipfile.ZipFile(publication.zip_path) as bundle:
        real_names = {name for name in bundle.namelist() if not name.endswith("/")}

    assert real_names == EXPECTED_PUBLICATION_FILES
    assert set(body["bundles"][0]["files"]) == real_names


def test_status_reports_mismatches_when_one_bundle_file_is_edited(
    settings, cleared_chapter: Chapter, publication
) -> None:
    _rewrite_zip_entry(publication.zip_path, "ban-dich.md", b"# bi sua tay\n")

    bundle = _status(settings, cleared_chapter.id)["bundles"][0]

    assert bundle["verified"] is False
    assert bundle["mismatches"] == ["ban-dich.md"]
    assert bundle["stale"] is False


def test_status_reports_a_missing_bundle_file_as_a_mismatch(
    settings, cleared_chapter: Chapter, publication
) -> None:
    _rewrite_zip_entry(publication.zip_path, "transcript.srt", None)

    bundle = _status(settings, cleared_chapter.id)["bundles"][0]

    assert bundle["verified"] is False
    assert bundle["mismatches"] == ["transcript.srt"]


def test_status_marks_rights_change_as_stale(
    settings, db_session, cleared_chapter: Chapter, publication
) -> None:
    _add_grant(db_session, cleared_chapter, RightsScope.DOWNLOAD, key="extra")

    bundle = _status(settings, cleared_chapter.id)["bundles"][0]

    assert bundle["stale"] is True
    assert bundle["staleReasons"] == ["RIGHTS_CHANGED"]
    assert bundle["verified"] is True


def test_status_marks_approved_master_change_as_stale(
    settings, db_session, artifact_store, cleared_chapter: Chapter, publication
) -> None:
    other_master = _write_artifact(
        db_session,
        artifact_store.resolve(),
        f"audio/{cleared_chapter.id}/masters/master-2.mp3",
        b"mp3 payload 2",
        f"018f0000-0000-7000-8000-{_suffix('cleared')}070",
        cleared_chapter.id,
        ArtifactKind.MASTER_MP3,
        "audio/mpeg",
        duration_ms=61_000,
        metadata={"translation_run_id": cleared_chapter.approved_translation_run_id},
    )
    cleared_chapter.approved_master_artifact_id = other_master.id
    db_session.commit()

    bundle = _status(settings, cleared_chapter.id)["bundles"][0]

    assert bundle["stale"] is True
    assert bundle["staleReasons"] == ["MASTER_CHANGED"]


def test_status_marks_approved_translation_change_as_stale(
    settings, db_session, cleared_chapter: Chapter, publication
) -> None:
    next_run = TranslationRun(
        id=f"018f0000-0000-7000-8000-{_suffix('cleared')}071",
        chapter_id=cleared_chapter.id,
        source_revision_id=cleared_chapter.active_source_revision_id,
        prompt_version="translation-v2",
        status=RunStatus.APPROVED.value,
        translation_text_sha256="f" * 64,
        estimated_cost_vnd=0,
        actual_cost_vnd=0,
    )
    db_session.add(next_run)
    db_session.flush()
    cleared_chapter.approved_translation_run_id = next_run.id
    db_session.commit()

    bundle = _status(settings, cleared_chapter.id)["bundles"][0]

    assert bundle["stale"] is True
    assert bundle["staleReasons"] == ["TRANSLATION_CHANGED"]


def test_status_marks_gate_blocked_when_publication_rights_expire(
    settings, db_session, cleared_chapter: Chapter, publication
) -> None:
    for grant in db_session.query(RightsGrant).all():
        grant.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()

    body = _status(settings, cleared_chapter.id)

    assert body["gate"]["allowed"] is False
    assert "PUBLIC_STREAM" in body["gate"]["reasons"]
    assert body["bundles"][0]["stale"] is True
    assert "GATE_BLOCKED" in body["bundles"][0]["staleReasons"]
    assert "RIGHTS_CHANGED" in body["bundles"][0]["staleReasons"]


def test_status_reports_unknown_provenance_instead_of_assuming_fresh(
    settings, cleared_chapter: Chapter, publication
) -> None:
    publication.zip_path.unlink()

    bundle = _status(settings, cleared_chapter.id)["bundles"][0]

    assert bundle["verified"] is False
    assert set(bundle["mismatches"]) == EXPECTED_PUBLICATION_FILES
    assert bundle["stale"] is True
    assert "UNKNOWN_MASTER_PROVENANCE" in bundle["staleReasons"]
    assert "UNKNOWN_TRANSLATION_PROVENANCE" in bundle["staleReasons"]


def test_status_blocks_publication_when_rights_are_not_cleared(
    settings, db_session, artifact_store, workflow
) -> None:
    chapter = _ready_chapter(
        db_session,
        artifact_store.resolve(),
        key="review",
        rights_status=RightsStatus.REVIEW_REQUIRED,
    )

    body = _status(settings, chapter.id)

    assert body["gate"]["allowed"] is False
    assert "RIGHTS_NOT_CLEARED" in body["gate"]["reasons"]
    assert body["bundles"] == []
    with pytest.raises(PermissionError, match="RIGHTS_NOT_CLEARED"):
        workflow.build_publication_bundle(
            chapter.id, PublicationMetadata("Tap mot", 1, False)
        )


def test_status_returns_404_for_an_unknown_chapter(settings, migrated_engine) -> None:
    with _client(settings) as client:
        response = client.get(
            "/api/chapters/018f0000-0000-7000-8000-0000000000ff/exports/status"
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "CHAPTER_NOT_FOUND"


def test_status_never_lists_bundles_of_another_chapter(
    settings, db_session, artifact_store, workflow, cleared_chapter, publication
) -> None:
    other = _ready_chapter(db_session, artifact_store.resolve(), key="other")
    other_export = workflow.build_publication_bundle(
        other.id, PublicationMetadata("Tap khac", 2, False)
    )

    body = _status(settings, cleared_chapter.id)
    ids = {bundle["id"] for bundle in body["bundles"]}

    assert publication.id in ids
    assert other_export.id not in ids
    assert all(bundle["files"] for bundle in body["bundles"])

    other_body = _status(settings, other.id)
    assert [bundle["id"] for bundle in other_body["bundles"]] == [other_export.id]


def test_private_archive_never_grants_public_export(
    settings, db_session, artifact_store, workflow
) -> None:
    chapter = _ready_chapter(
        db_session,
        artifact_store.resolve(),
        key="private",
        grant_scopes=(RightsScope.TRANSLATE_VI, RightsScope.CREATE_AUDIO),
    )
    private_export = workflow.build_private_archive(chapter.id)

    body = _status(settings, chapter.id)

    assert body["gate"]["allowed"] is False
    assert "PUBLIC_STREAM" in body["gate"]["reasons"]
    assert [bundle["kind"] for bundle in body["bundles"]] == ["PRIVATE_ARCHIVE"]
    assert body["bundles"][0]["id"] == private_export.id
    assert body["bundles"][0]["stale"] is False
    assert "metadata.json" not in body["bundles"][0]["files"]
    assert "PRIVATE_ONLY.txt" in body["bundles"][0]["files"]


def test_status_lists_the_newest_bundle_of_each_kind_first(
    settings, db_session, workflow, cleared_chapter: Chapter, publication
) -> None:
    private_export = workflow.build_private_archive(cleared_chapter.id)
    row = db_session.get(Export, private_export.id)
    publication_row = db_session.get(Export, publication.id)
    row.created_at = publication_row.created_at + timedelta(hours=1)
    db_session.commit()

    body = _status(settings, cleared_chapter.id)

    assert [bundle["id"] for bundle in body["bundles"]] == [
        private_export.id,
        publication.id,
    ]
    assert [bundle["kind"] for bundle in body["bundles"]] == [
        "PRIVATE_ARCHIVE",
        "PUBLICATION_BUNDLE",
    ]


def test_status_route_never_opens_a_network_connection(
    settings, cleared_chapter: Chapter, publication, monkeypatch: pytest.MonkeyPatch
) -> None:
    outbound: list[object] = []
    real_connect = socket.socket.connect

    def recording_connect(self: socket.socket, address: object) -> None:
        host = address[0] if isinstance(address, tuple) and address else address
        # asyncio's event-loop self-pipe connects over loopback; anything else
        # would be real outbound traffic, which this route must never produce.
        if host not in {"127.0.0.1", "::1", "localhost"}:
            outbound.append(address)
        return real_connect(self, address)

    def forbidden_create_connection(*args: object, **kwargs: object) -> None:
        outbound.append(args[0] if args else kwargs)
        raise AssertionError("NETWORK_ACCESS_NOT_ALLOWED")

    def forbidden_request(*args: object, **kwargs: object) -> None:
        outbound.append(args[0] if args else kwargs)
        raise AssertionError("CLOUD_REQUEST_NOT_ALLOWED")

    monkeypatch.setattr(socket.socket, "connect", recording_connect)
    monkeypatch.setattr(socket, "create_connection", forbidden_create_connection)
    # Only real HTTP transports are poisoned; the in-process TestClient uses an
    # ASGI transport, so this cannot be satisfied by the test harness itself.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden_request)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", forbidden_request
    )

    bundle = _status(settings, cleared_chapter.id)["bundles"][0]

    assert bundle["verified"] is True
    assert outbound == []


def test_legacy_gate_route_still_answers(settings, cleared_chapter: Chapter) -> None:
    with _client(settings) as client:
        response = client.get(f"/api/chapters/{cleared_chapter.id}/exports/gate")

    assert response.status_code == 200
    assert response.json()["allowed"] is True


def publication_rights_hash(publication) -> str:
    with zipfile.ZipFile(publication.zip_path) as bundle:
        return json.loads(bundle.read("provenance.json"))["rights"]["evaluation_hash"]


def _status(settings, chapter_id: str) -> dict:
    with _client(settings) as client:
        response = client.get(f"/api/chapters/{chapter_id}/exports/status")

    assert response.status_code == 200, response.text
    return response.json()


def _client(settings) -> TestClient:
    return TestClient(
        create_app(settings=settings, acquire_lock=False),
        base_url="http://127.0.0.1:8765",
    )


def _rewrite_zip_entry(
    zip_path: Path, name: str, payload: bytes | None
) -> None:
    with zipfile.ZipFile(zip_path) as bundle:
        entries = {
            entry.filename: bundle.read(entry.filename)
            for entry in bundle.infolist()
            if not entry.is_dir()
        }
    if payload is None:
        entries.pop(name, None)
    else:
        entries[name] = payload
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for filename, content in entries.items():
            bundle.writestr(filename, content)


class DeterministicProbe:
    async def probe(
        self, path: Path, expected_sha256: str | None = None
    ) -> MasterResult:
        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected_sha256 is not None and sha256 != expected_sha256:
            raise ValueError("MASTER_CHECKSUM_MISMATCH")
        return MasterResult(
            duration_ms=61_000,
            sha256=sha256,
            codec="mp3",
            sample_rate=44_100,
            channels=1,
            bitrate_kbps=128,
            integrated_lufs=-16.0,
            true_peak_dbtp=-1.5,
        )


def _ready_chapter(
    db_session,
    artifact_root: Path,
    *,
    key: str,
    rights_status: RightsStatus = RightsStatus.CLEARED,
    grant_scopes: tuple[RightsScope, ...] = (
        RightsScope.TRANSLATE_VI,
        RightsScope.CREATE_AUDIO,
        RightsScope.PUBLIC_STREAM,
    ),
) -> Chapter:
    suffix = _suffix(key)
    project = Project(
        id=f"018f0000-0000-7000-8000-{suffix}001",
        title=f"Truyen {key}",
        slug=f"truyen-{suffix}",
        source_type=SourceType.LICENSED_PARTNER.value,
        source_reference_url="https://example.test/source",
        rights_status=rights_status.value,
        target_language="vi-VN",
    )
    chapter = Chapter(
        id=f"018f0000-0000-7000-8000-{suffix}002",
        project_id=project.id,
        ordinal=1,
        source_title="Mot",
        translated_title="Tap mot",
        state=ChapterState.READY_TO_EXPORT.value,
    )
    revision = SourceRevision(
        id=f"018f0000-0000-7000-8000-{suffix}003",
        chapter_id=chapter.id,
        revision_no=1,
        import_kind=ImportKind.PASTE.value,
        normalized_text="source text",
        normalized_sha256=_sha("source text"),
        han_char_count=0,
        total_char_count=11,
        normalizer_version="nfc-v1",
    )
    run = TranslationRun(
        id=f"018f0000-0000-7000-8000-{suffix}004",
        chapter_id=chapter.id,
        source_revision_id=revision.id,
        prompt_version="translation-v1",
        status=RunStatus.APPROVED.value,
        translation_text_sha256=_sha("Ban dich mot.\nBan dich hai."),
        estimated_cost_vnd=1200,
        actual_cost_vnd=1000,
    )
    db_session.add(project)
    db_session.flush()
    db_session.add_all((chapter, revision, run))
    db_session.flush()
    chapter.active_source_revision_id = revision.id
    chapter.approved_translation_run_id = run.id

    segment_ids = []
    for index, text in enumerate(("Ban dich mot.", "Ban dich hai."), start=1):
        source = SourceSegment(
            id=f"018f0000-0000-7000-8000-{suffix}{10 + index:03d}",
            source_revision_id=revision.id,
            segment_index=index,
            paragraph_start=index,
            paragraph_end=index,
            source_text=f"source {index}",
            source_sha256=_sha(f"source {index}"),
            segment_kind="SOURCE",
        )
        segment = TranslationSegment(
            id=f"018f0000-0000-7000-8000-{suffix}{20 + index:03d}",
            translation_run_id=run.id,
            source_segment_id=source.id,
            target_text=text,
            target_sha256=_sha(text),
            was_cache_hit=False,
            manually_edited=False,
        )
        db_session.add_all((source, segment))
        segment_ids.append(segment.id)

    _write_artifact(
        db_session,
        artifact_root,
        f"models/licenses/{suffix}-local-tts.txt",
        b"Local TTS license",
        f"018f0000-0000-7000-8000-{suffix}090",
        None,
        ArtifactKind.LICENSE_SNAPSHOT,
        "text/plain; charset=utf-8",
    )
    db_session.flush()
    preset = VoicePreset(
        id=f"018f0000-0000-7000-8000-{suffix}030",
        name="Narrator",
        locale="vi-VN",
        origin="BUILT_IN",
        speed="1.0",
        pitch="0",
        sample_rate=44_100,
        active=True,
        license_snapshot_artifact_id=f"018f0000-0000-7000-8000-{suffix}090",
    )
    plan = VoicePlan(
        id=f"018f0000-0000-7000-8000-{suffix}031",
        chapter_id=chapter.id,
        revision_no=1,
        mode="SINGLE_NARRATOR",
        narrator_preset_id=preset.id,
        plan_sha256="2" * 64,
    )
    role = VoiceRole(
        id=f"018f0000-0000-7000-8000-{suffix}032",
        voice_plan_id=plan.id,
        role_key="narrator",
        display_name="Narrator",
        voice_preset_id=preset.id,
        is_narrator=True,
    )
    db_session.add(preset)
    db_session.flush()
    db_session.add_all((plan, role))
    db_session.flush()
    chapter.active_voice_plan_id = plan.id
    for index, text in enumerate(("Ban dich mot.", "Ban dich hai."), start=1):
        db_session.add(
            SpeechSegment(
                id=f"018f0000-0000-7000-8000-{suffix}{40 + index:03d}",
                chapter_id=chapter.id,
                translation_run_id=run.id,
                voice_plan_id=plan.id,
                segment_index=index,
                translation_segment_id=segment_ids[index - 1],
                role_id=role.id,
                narration_text=text,
                narration_sha256=_sha(text),
                pause_after_ms=500,
            )
        )

    _write_artifact(
        db_session,
        artifact_root,
        f"audio/{chapter.id}/masters/master.mp3",
        b"mp3 payload",
        f"018f0000-0000-7000-8000-{suffix}050",
        chapter.id,
        ArtifactKind.MASTER_MP3,
        "audio/mpeg",
        duration_ms=61_000,
        metadata={
            "codec": "mp3",
            "sample_rate": 44100,
            "channels": 1,
            "bitrate_kbps": 128,
            "translation_run_id": run.id,
            "voice_plan_id": plan.id,
        },
    )
    _write_artifact(
        db_session,
        artifact_root,
        f"audio/{chapter.id}/subtitles/transcript.srt",
        b"1\n00:00:00,000 --> 00:01:01,000\nBan dich mot.\n",
        f"018f0000-0000-7000-8000-{suffix}051",
        chapter.id,
        ArtifactKind.SRT,
        "application/x-subrip",
        duration_ms=61_000,
    )
    chapter.approved_master_artifact_id = f"018f0000-0000-7000-8000-{suffix}050"
    _rights(db_session, project.id, suffix, grant_scopes)
    db_session.commit()
    return chapter


def _add_grant(db_session, chapter: Chapter, scope: RightsScope, *, key: str) -> None:
    suffix = _suffix(key)
    evidence = RightsEvidence(
        id=f"018f0000-0000-7000-8000-{suffix}060",
        project_id=chapter.project_id,
        evidence_kind="CONTRACT",
        display_name=f"{scope.value}-{key}.txt",
        relative_path=f"evidence/{scope.value}-{key}.txt",
        sha256=_sha(key),
    )
    db_session.add(evidence)
    db_session.add(
        RightsGrant(
            id=f"018f0000-0000-7000-8000-{suffix}061",
            project_id=chapter.project_id,
            scope=scope.value,
            territory="VN",
            allows_ai_processing=True,
            allows_third_party_cloud=False,
            valid_from=NOW - timedelta(days=3650),
            expires_at=NOW + timedelta(days=3650),
            evidence_id=evidence.id,
        )
    )
    db_session.commit()


def _rights(
    db_session, project_id: str, suffix: str, scopes: tuple[RightsScope, ...]
) -> None:
    evidence = RightsEvidence(
        id=f"018f0000-0000-7000-8000-{suffix}060",
        project_id=project_id,
        evidence_kind="CONTRACT",
        display_name="contract.txt",
        relative_path="evidence/contract.txt",
        sha256="1" * 64,
    )
    db_session.add(evidence)
    for index, scope in enumerate(scopes, start=61):
        db_session.add(
            RightsGrant(
                id=f"018f0000-0000-7000-8000-{suffix}{index:03d}",
                project_id=project_id,
                scope=scope.value,
                territory="VN",
                allows_ai_processing=True,
                allows_third_party_cloud=False,
                valid_from=NOW - timedelta(days=3650),
                expires_at=NOW + timedelta(days=3650),
                evidence_id=evidence.id,
            )
        )


def _write_artifact(
    db_session,
    artifact_root: Path,
    relative_path: str,
    payload: bytes,
    artifact_id: str,
    chapter_id: str | None,
    kind: ArtifactKind,
    mime_type: str,
    *,
    duration_ms: int | None = None,
    metadata: dict[str, object] | None = None,
) -> Artifact:
    path = artifact_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    artifact = Artifact(
        id=artifact_id,
        chapter_id=chapter_id,
        kind=kind.value,
        status=ArtifactStatus.READY.value,
        relative_path=relative_path,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        mime_type=mime_type,
        duration_ms=duration_ms,
        input_hash=_sha(relative_path),
        settings_hash=_sha(kind.value),
        metadata_json=metadata,
    )
    db_session.add(artifact)
    return artifact


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _suffix(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:9]
