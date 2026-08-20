from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import PureWindowsPath
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import ArtifactKind, ArtifactStatus, ChapterState, ImportKind, RightsStatus, SourceType, new_id
from app.db.models import Artifact, Chapter, Project, SourceRevision
from app.modules.artifacts.store import ArtifactAlreadyExists, ArtifactStore, ArtifactWrite
from app.modules.projects.state_machine import next_state
from app.modules.sources.normalize import NormalizedSource, normalize_source
from app.modules.sources.paste_txt import decode_txt


ZERO_HASH = "0" * 64


@dataclass(frozen=True)
class CreateProject:
    title: str
    slug: str
    source_type: SourceType
    rights_status: RightsStatus
    source_reference_url: str | None
    style_guide_text: str | None


@dataclass(frozen=True)
class ImportItem:
    ordinal: int
    title: str | None
    text: str | None = None
    filename: str | None = None
    payload: bytes | None = None


@dataclass(frozen=True)
class ImportChapters:
    kind: ImportKind
    items: tuple[ImportItem, ...]

    @classmethod
    def paste(cls, ordinal: int, title: str | None, text: str) -> ImportChapters:
        return cls(ImportKind.PASTE, (ImportItem(ordinal=ordinal, title=title, text=text),))

    @classmethod
    def txt(cls, ordinal: int, filename: str, payload: bytes, title: str | None = None) -> ImportChapters:
        return cls(ImportKind.TXT, (ImportItem(ordinal=ordinal, title=title or _safe_basename(filename), filename=filename, payload=payload),))


@dataclass(frozen=True)
class ProjectView:
    id: str
    title: str
    slug: str
    source_type: SourceType
    rights_status: RightsStatus
    source_reference_url: str | None
    style_guide_text: str | None


@dataclass(frozen=True)
class ChapterView:
    id: str
    project_id: str
    ordinal: int
    source_title: str | None
    state: ChapterState
    active_source_revision_id: str
    approved_translation_run_id: str | None
    active_voice_plan_id: str | None
    approved_master_artifact_id: str | None
    last_export_id: str | None


@dataclass(frozen=True)
class SourceRevisionView:
    id: str
    chapter_id: str
    revision_no: int
    normalized_text: str
    normalized_sha256: str
    raw_artifact_id: str | None


class ProjectWorkflow:
    def __init__(self, session: Session, artifact_store: ArtifactStore) -> None:
        self.session = session
        self.artifact_store = artifact_store

    def create_project(self, command: CreateProject) -> ProjectView:
        title = command.title.strip()
        slug = command.slug.strip()
        if not title:
            raise ValueError("PROJECT_TITLE_REQUIRED")
        if not slug:
            raise ValueError("PROJECT_SLUG_REQUIRED")
        source_reference_url = _validate_reference_url(command.source_reference_url)
        project = Project(
            id=new_id(),
            title=title,
            slug=slug,
            source_type=command.source_type.name,
            rights_status=command.rights_status.name,
            source_reference_url=source_reference_url,
            style_guide_text=command.style_guide_text,
        )
        self.session.add(project)
        self.session.commit()
        return _project_view(project)

    def import_chapters(self, project_id: str, command: ImportChapters) -> tuple[ChapterView, ...]:
        if command.kind not in {ImportKind.PASTE, ImportKind.TXT}:
            raise ValueError("IMPORT_KIND_UNSUPPORTED")
        project = self.session.get(Project, project_id)
        if project is None:
            raise ValueError("PROJECT_NOT_FOUND")
        views: list[ChapterView] = []
        for item in command.items:
            raw_text, original_filename = self._read_item(command.kind, item)
            normalized = normalize_source(raw_text)
            chapter = self._get_or_create_chapter(project_id, item)
            revision = self._revision_for(chapter, command.kind, normalized)
            if revision is None:
                revision = self._create_revision(project, chapter, command.kind, item, raw_text, normalized, original_filename)
            changed_active_revision = chapter.active_source_revision_id != revision.id
            chapter.active_source_revision_id = revision.id
            chapter.source_title = item.title
            chapter.state = ChapterState.NORMALIZED.name
            if changed_active_revision:
                self._invalidate_downstream(chapter)
            self.session.flush()
            views.append(_chapter_view(chapter))
        self.session.commit()
        return tuple(views)

    def request_transition(self, chapter_id: str, requested: ChapterState) -> ChapterView:
        chapter = self.session.get(Chapter, chapter_id)
        if chapter is None:
            raise ValueError("CHAPTER_NOT_FOUND")
        chapter.state = next_state(ChapterState(chapter.state), requested).name
        self.session.commit()
        return _chapter_view(chapter)

    def get_source_revision(self, revision_id: str) -> SourceRevisionView:
        revision = self.session.get(SourceRevision, revision_id)
        if revision is None:
            raise ValueError("SOURCE_REVISION_NOT_FOUND")
        return _revision_view(revision)

    def get_workspace(self, project_id: str) -> dict[str, object]:
        project = self.session.get(Project, project_id)
        if project is None:
            raise ValueError("PROJECT_NOT_FOUND")
        chapters = self.session.scalars(select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.ordinal)).all()
        return {"project": _project_view(project), "chapters": tuple(_chapter_view(chapter) for chapter in chapters)}

    def _read_item(self, kind: ImportKind, item: ImportItem) -> tuple[str, str | None]:
        if item.ordinal <= 0:
            raise ValueError("CHAPTER_ORDINAL_INVALID")
        if kind is ImportKind.PASTE:
            if item.text is None:
                raise ValueError("PASTE_TEXT_REQUIRED")
            return item.text, None
        if item.payload is None:
            raise ValueError("TXT_PAYLOAD_REQUIRED")
        decoded = decode_txt(item.payload)
        return decoded.text, _safe_basename(item.filename or "source.txt")

    def _get_or_create_chapter(self, project_id: str, item: ImportItem) -> Chapter:
        chapter = self.session.scalar(
            select(Chapter).where(Chapter.project_id == project_id, Chapter.ordinal == item.ordinal)
        )
        if chapter is not None:
            return chapter
        chapter = Chapter(id=new_id(), project_id=project_id, ordinal=item.ordinal, source_title=item.title, state=ChapterState.IMPORTED.name)
        self.session.add(chapter)
        self.session.flush()
        return chapter

    def _revision_for(self, chapter: Chapter, kind: ImportKind, normalized: NormalizedSource) -> SourceRevision | None:
        return self.session.scalar(
            select(SourceRevision).where(
                SourceRevision.chapter_id == chapter.id,
                SourceRevision.normalized_sha256 == normalized.sha256,
                SourceRevision.import_kind == kind.name,
            )
        )

    def _create_revision(
        self,
        project: Project,
        chapter: Chapter,
        kind: ImportKind,
        item: ImportItem,
        raw_text: str,
        normalized: NormalizedSource,
        original_filename: str | None,
    ) -> SourceRevision:
        revision_no = (self.session.scalar(select(func.max(SourceRevision.revision_no)).where(SourceRevision.chapter_id == chapter.id)) or 0) + 1
        raw_bytes = raw_text.encode("utf-8")
        artifact = self._store_source_artifact(project, chapter, kind, revision_no, raw_bytes, normalized.sha256)
        revision = SourceRevision(
            id=new_id(),
            chapter_id=chapter.id,
            revision_no=revision_no,
            import_kind=kind.name,
            original_filename=original_filename,
            source_reference_url=project.source_reference_url,
            raw_artifact_id=artifact.id,
            normalized_text=normalized.text,
            normalized_sha256=normalized.sha256,
            han_char_count=normalized.han_char_count,
            total_char_count=normalized.total_char_count,
            normalizer_version=normalized.normalizer_version,
        )
        self.session.add(revision)
        self.session.flush()
        return revision

    def _store_source_artifact(
        self,
        project: Project,
        chapter: Chapter,
        kind: ImportKind,
        revision_no: int,
        payload: bytes,
        normalized_sha256: str,
    ) -> Artifact:
        relative_path = f"projects/{project.slug}/sources/chapter-{chapter.ordinal:04d}/revision-{revision_no:04d}.txt"
        write = ArtifactWrite(
            kind=ArtifactKind.SOURCE_SNAPSHOT,
            relative_path=relative_path,
            input_hash=normalized_sha256,
            settings_hash=hashlib.sha256(f"{kind.name}:nfc-v1".encode("utf-8")).hexdigest(),
            mime_type="text/plain; charset=utf-8",
        )
        with self.artifact_store.begin(write) as writer:
            writer.file.write(payload)
            try:
                stored = writer.commit()
            except ArtifactAlreadyExists as exc:
                raise ValueError("SOURCE_SNAPSHOT_ALREADY_EXISTS") from exc
        artifact = Artifact(
            id=new_id(),
            chapter_id=chapter.id,
            kind=ArtifactKind.SOURCE_SNAPSHOT.name,
            status=ArtifactStatus.READY.name,
            relative_path=stored.relative_path,
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            mime_type=write.mime_type,
            input_hash=write.input_hash,
            settings_hash=write.settings_hash,
            producer="ProjectWorkflow",
            producer_version="task1",
        )
        self.session.add(artifact)
        self.session.flush()
        return artifact

    def _invalidate_downstream(self, chapter: Chapter) -> None:
        chapter.approved_translation_run_id = None
        chapter.active_voice_plan_id = None
        chapter.approved_master_artifact_id = None
        chapter.last_export_id = None
        chapter.translation_approved_at = None
        chapter.audio_approved_at = None


def _validate_reference_url(source_reference_url: str | None) -> str | None:
    if source_reference_url is None or source_reference_url.strip() == "":
        return None
    parsed = urlparse(source_reference_url.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("SOURCE_REFERENCE_URL_HTTPS_REQUIRED")
    return source_reference_url.strip()


def _safe_basename(filename: str) -> str:
    return PureWindowsPath(filename).name or "source.txt"


def _project_view(project: Project) -> ProjectView:
    return ProjectView(
        id=project.id,
        title=project.title,
        slug=project.slug,
        source_type=SourceType(project.source_type),
        rights_status=RightsStatus(project.rights_status),
        source_reference_url=project.source_reference_url,
        style_guide_text=project.style_guide_text,
    )


def _chapter_view(chapter: Chapter) -> ChapterView:
    if chapter.active_source_revision_id is None:
        raise ValueError("CHAPTER_ACTIVE_SOURCE_REVISION_REQUIRED")
    return ChapterView(
        id=chapter.id,
        project_id=chapter.project_id,
        ordinal=chapter.ordinal,
        source_title=chapter.source_title,
        state=ChapterState(chapter.state),
        active_source_revision_id=chapter.active_source_revision_id,
        approved_translation_run_id=chapter.approved_translation_run_id,
        active_voice_plan_id=chapter.active_voice_plan_id,
        approved_master_artifact_id=chapter.approved_master_artifact_id,
        last_export_id=chapter.last_export_id,
    )


def _revision_view(revision: SourceRevision) -> SourceRevisionView:
    return SourceRevisionView(
        id=revision.id,
        chapter_id=revision.chapter_id,
        revision_no=revision.revision_no,
        normalized_text=revision.normalized_text,
        normalized_sha256=revision.normalized_sha256,
        raw_artifact_id=revision.raw_artifact_id,
    )
