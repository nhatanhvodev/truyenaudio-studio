from __future__ import annotations

from dataclasses import dataclass, field
import socket
import urllib.request

import pytest

from app.contracts import ArtifactStatus, ChapterState, ExportKind, ExportStatus, RightsStatus, RunStatus, SourceType, VoiceMode, VoiceOrigin
from app.db.models import Artifact, Chapter, Export, TranslationRun, VoicePlan, VoicePreset
from app.modules.projects.workflow import CreateProject, ImportChapters, ProjectWorkflow


@dataclass
class NetworkRecorder:
    calls: list[object] = field(default_factory=list)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> NetworkRecorder:
    recorder = NetworkRecorder()

    def record_connect(self: socket.socket, address: object) -> None:
        recorder.calls.append(("connect", address))
        raise AssertionError(f"unexpected network connect to {address!r}")

    def record_urlopen(url: object, *args: object, **kwargs: object) -> object:
        recorder.calls.append(("urlopen", url))
        raise AssertionError(f"unexpected urlopen for {url!r}")

    monkeypatch.setattr(socket.socket, "connect", record_connect)
    monkeypatch.setattr(urllib.request, "urlopen", record_urlopen)
    return recorder


@pytest.fixture
def workflow(db_session, artifact_store) -> ProjectWorkflow:
    return ProjectWorkflow(db_session, artifact_store)


@pytest.fixture
def project(workflow: ProjectWorkflow):
    return workflow.create_project(
        CreateProject(
            "Truyen",
            "truyen",
            SourceType.USER_SUPPLIED_PRIVATE,
            RightsStatus.PRIVATE_ONLY,
            None,
            "dich sat nghia",
        )
    )


def test_paste_import_never_fetches_reference_url(workflow: ProjectWorkflow, no_network: NetworkRecorder) -> None:
    project = workflow.create_project(
        CreateProject(
            "Truyen",
            "truyen-url",
            SourceType.USER_SUPPLIED_PRIVATE,
            RightsStatus.PRIVATE_ONLY,
            "https://wenku.read.qq.com/book/1",
            "dich sat nghia",
        )
    )

    (chapter,) = workflow.import_chapters(project.id, ImportChapters.paste(1, "第一章", "你好。"))

    assert chapter.state is ChapterState.NORMALIZED
    assert no_network.calls == []


def test_same_hash_reuses_revision_but_changed_text_creates_next(workflow: ProjectWorkflow, project) -> None:
    first = workflow.import_chapters(project.id, ImportChapters.paste(1, "一", "甲。"))[0]
    same = workflow.import_chapters(project.id, ImportChapters.paste(1, "一", "甲。"))[0]
    changed = workflow.import_chapters(project.id, ImportChapters.paste(1, "一", "乙。"))[0]

    assert same.active_source_revision_id == first.active_source_revision_id
    assert changed.active_source_revision_id != first.active_source_revision_id


def test_changed_source_revision_invalidates_active_downstream_pointers(
    workflow: ProjectWorkflow,
    project,
    db_session,
) -> None:
    first = workflow.import_chapters(project.id, ImportChapters.paste(1, "一", "甲。"))[0]
    stored = db_session.get(Chapter, first.id)
    assert stored is not None
    _attach_downstream_outputs(db_session, stored, first.active_source_revision_id)
    db_session.commit()

    changed = workflow.import_chapters(project.id, ImportChapters.paste(1, "一", "乙。"))[0]

    assert changed.active_source_revision_id != first.active_source_revision_id
    assert changed.approved_translation_run_id is None
    assert changed.active_voice_plan_id is None
    assert changed.approved_master_artifact_id is None
    assert changed.last_export_id is None


def _attach_downstream_outputs(db_session, chapter: Chapter, source_revision_id: str) -> None:
    translation_run_id = "018f0000-0000-7000-8000-000000000101"
    voice_preset_id = "018f0000-0000-7000-8000-000000000102"
    voice_plan_id = "018f0000-0000-7000-8000-000000000103"
    artifact_id = "018f0000-0000-7000-8000-000000000104"
    export_id = "018f0000-0000-7000-8000-000000000105"

    db_session.add(
        TranslationRun(
            id=translation_run_id,
            chapter_id=chapter.id,
            source_revision_id=source_revision_id,
            prompt_version="v1",
            status=RunStatus.APPROVED.name,
        )
    )
    db_session.add(
        VoicePreset(
            id=voice_preset_id,
            name="Narrator",
            locale="vi-VN",
            origin=VoiceOrigin.BUILT_IN.name,
            speed="1.0",
            pitch="0",
            sample_rate=44100,
            active=True,
        )
    )
    db_session.flush()
    db_session.add(
        VoicePlan(
            id=voice_plan_id,
            chapter_id=chapter.id,
            revision_no=1,
            mode=VoiceMode.SINGLE_NARRATOR.name,
            narrator_preset_id=voice_preset_id,
            plan_sha256="1" * 64,
        )
    )
    db_session.add(
        Artifact(
            id=artifact_id,
            chapter_id=chapter.id,
            kind="MASTER_MP3",
            status=ArtifactStatus.READY.name,
            relative_path="projects/truyen/audio/chapter-0001.mp3",
            sha256="2" * 64,
            byte_size=1,
            mime_type="audio/mpeg",
            input_hash="3" * 64,
            settings_hash="4" * 64,
        )
    )
    db_session.flush()
    db_session.add(
        Export(
            id=export_id,
            chapter_id=chapter.id,
            kind=ExportKind.PUBLICATION_BUNDLE.name,
            status=ExportStatus.READY.name,
            bundle_artifact_id=artifact_id,
            manifest_sha256="5" * 64,
        )
    )
    db_session.flush()
    chapter.approved_translation_run_id = translation_run_id
    chapter.active_voice_plan_id = voice_plan_id
    chapter.approved_master_artifact_id = artifact_id
    chapter.last_export_id = export_id


def test_txt_import_accepts_utf8_and_rejects_utf16_without_bom(workflow: ProjectWorkflow, project) -> None:
    (chapter,) = workflow.import_chapters(
        project.id,
        ImportChapters.txt(1, "one.txt", "第一章\r\n你好。".encode("utf-8")),
    )

    assert chapter.state is ChapterState.NORMALIZED

    with pytest.raises(ValueError, match="INPUT_ENCODING_CONFIRMATION_REQUIRED"):
        workflow.import_chapters(project.id, ImportChapters.txt(2, "two.txt", "第二章".encode("utf-16-le")))


def test_source_normalization_preserves_digits_and_limits_blank_lines(workflow: ProjectWorkflow, project) -> None:
    (chapter,) = workflow.import_chapters(
        project.id,
        ImportChapters.paste(1, "一", "第12章  张三  \r\n\r\n\r\n\r\n你好。  "),
    )

    revision = workflow.get_source_revision(chapter.active_source_revision_id)
    assert revision.normalized_text == "第12章  张三\n\n\n你好。"
