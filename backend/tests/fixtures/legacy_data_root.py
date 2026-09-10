"""R01 shared fixture: a real data root parked at a pre-head revision.

Used by backend/tests/db/test_migration_rehearsal.py (upgrade drill) and
backend/tests/storage/test_backup_rollback_drill.py (backup/rollback drill), so both
drills run against the *same* legacy shape: a project with three chapters, each with a
source revision, two source segments, an approved translation run with two translated
segments, and one READY MASTER_MP3 artifact that exists on disk with a matching sha256.

Everything is written through the real ORM into a temporary data root. No cloud call,
no model download, and nothing touches the studio data root.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config

from app.contracts import (
    ArtifactKind,
    ArtifactStatus,
    ChapterState,
    ImportKind,
    RightsStatus,
    RunStatus,
    SourceType,
)
from app.db.base import create_engine_for, session_factory
from app.db.models import (
    Artifact,
    Chapter,
    Project,
    SourceRevision,
    SourceSegment,
    TranslationRun,
    TranslationSegment,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = BACKEND_ROOT / "migrations"

#: Two additive migrations behind head: 0017 (voice preview jobs) and 0018 (JobKind.SUMMARIZE).
LEGACY_REVISION = "0016"
LEGACY_HEAD = "0018"

PROJECT_ID = "11111111-1111-7111-8111-111111111111"
PROJECT_TITLE = "Truyen cu truoc nang cap"
CHAPTER_ORDINALS = (1, 2, 3)

LEGACY_TABLES = (
    "projects",
    "chapters",
    "source_revisions",
    "source_segments",
    "translation_runs",
    "translation_segments",
    "artifacts",
)


def chapter_id(ordinal: int) -> str:
    return f"22222222-2222-7222-8222-2222222222{ordinal:02d}"


def run_id(ordinal: int) -> str:
    return f"55555555-5555-7555-8555-5555555555{ordinal:02d}"


@dataclass(frozen=True)
class LegacySeed:
    """What the legacy data root contains, captured before any upgrade."""

    data_root: Path
    db_path: Path
    counts: dict[str, int]
    artifact_relative_path: str
    artifact_sha256: str
    artifact_bytes: bytes

    @property
    def artifact_file(self) -> Path:
        """On-disk location: artifacts are stored under <data root>/artifacts."""
        return self.data_root / "artifacts" / self.artifact_relative_path


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def alembic_config(db_path: Path, script_location: Path | None = None) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(script_location or MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return config


def upgrade(db_path: Path, revision: str, script_location: Path | None = None) -> None:
    command.upgrade(alembic_config(db_path, script_location), revision)


def table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        return {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }


def row_counts(db_path: Path, tables: tuple[str, ...] = LEGACY_TABLES) -> dict[str, int]:
    with sqlite3.connect(db_path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def integrity(db_path: Path) -> str:
    with sqlite3.connect(db_path) as connection:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def sqlite_content_sha256(db_path: Path) -> str:
    """Canonical *content* digest: sha256 of the sqlite logical dump.

    Byte-identity is the wrong test for a restore: the sqlite online-backup API
    rewrites the file, so the restored file is not necessarily byte-identical to the
    backup file. The logical dump is, and that is what the claims are about.
    """
    with sqlite3.connect(db_path) as connection:
        dump = chr(10).join(connection.iterdump())
    return hashlib.sha256(dump.encode("utf-8")).hexdigest()


def build_legacy_data_root(data_root: Path, *, revision: str = LEGACY_REVISION) -> LegacySeed:
    """Create a data root at the legacy revision and fill it with real rows."""
    data_root.mkdir(parents=True, exist_ok=True)
    db_path = data_root / "studio.sqlite3"
    upgrade(db_path, revision)

    artifact_payload = b"legacy approved master bytes"
    artifact_relative = "chapters/1/master.mp3"
    artifact_file = data_root / "artifacts" / artifact_relative
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    artifact_file.write_bytes(artifact_payload)

    engine = create_engine_for(db_path)
    try:
        with session_factory(engine)() as session:
            project = Project(
                id=PROJECT_ID,
                title=PROJECT_TITLE,
                slug="truyen-cu-truoc-nang-cap",
                source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
                rights_status=RightsStatus.PRIVATE_ONLY.value,
            )
            session.add(project)
            session.flush()
            for ordinal in CHAPTER_ORDINALS:
                chapter = Chapter(
                    id=chapter_id(ordinal),
                    project_id=project.id,
                    ordinal=ordinal,
                    source_title=f"Chuong {ordinal}",
                    translated_title=f"Chuong dich {ordinal}",
                    state=ChapterState.NORMALIZED.value,
                )
                session.add(chapter)
                session.flush()
                revision_row = SourceRevision(
                    id=f"33333333-3333-7333-8333-3333333333{ordinal:02d}",
                    chapter_id=chapter.id,
                    revision_no=1,
                    import_kind=ImportKind.PASTE.value,
                    normalized_text=f"nguyen van {ordinal}",
                    normalized_sha256=sha256_bytes(f"nguyen van {ordinal}".encode()),
                    han_char_count=0,
                    total_char_count=11,
                    normalizer_version="nfc-v1",
                )
                session.add(revision_row)
                session.flush()
                chapter.active_source_revision_id = revision_row.id
                segments = []
                for index in (1, 2):
                    segment = SourceSegment(
                        id=f"44444444-4444-7444-8444-4444444444{ordinal}{index}",
                        source_revision_id=revision_row.id,
                        segment_index=index,
                        paragraph_start=index - 1,
                        paragraph_end=index - 1,
                        source_text=f"doan {ordinal}.{index}",
                        source_sha256=sha256_bytes(f"doan {ordinal}.{index}".encode()),
                        segment_kind="SOURCE",
                    )
                    session.add(segment)
                    segments.append(segment)
                session.flush()
                run = TranslationRun(
                    id=run_id(ordinal),
                    chapter_id=chapter.id,
                    source_revision_id=revision_row.id,
                    prompt_version="translation-v1",
                    status=RunStatus.APPROVED.value,
                    translation_text_sha256=sha256_bytes(f"ban dich {ordinal}".encode()),
                )
                session.add(run)
                session.flush()
                chapter.approved_translation_run_id = run.id
                for index, segment in enumerate(segments, start=1):
                    session.add(
                        TranslationSegment(
                            id=f"66666666-6666-7666-8666-6666666666{ordinal}{index}",
                            translation_run_id=run.id,
                            source_segment_id=segment.id,
                            target_text=f"ban dich {ordinal}.{index}",
                            target_sha256=sha256_bytes(f"ban dich {ordinal}.{index}".encode()),
                        )
                    )
                session.add(
                    Artifact(
                        id=f"77777777-7777-7777-8777-7777777777{ordinal:02d}",
                        chapter_id=chapter.id,
                        kind=ArtifactKind.MASTER_MP3.value,
                        status=ArtifactStatus.READY.value,
                        relative_path=artifact_relative,
                        sha256=sha256_bytes(artifact_payload),
                        byte_size=len(artifact_payload),
                        mime_type="audio/mpeg",
                        duration_ms=61_000,
                        input_hash=sha256_bytes(f"input:{ordinal}".encode()),
                        settings_hash=sha256_bytes(f"settings:{ordinal}".encode()),
                        metadata_json={"codec": "mp3", "translation_run_id": run.id},
                    )
                )
            session.commit()
    finally:
        engine.dispose()

    return LegacySeed(
        data_root=data_root,
        db_path=db_path,
        counts=row_counts(db_path),
        artifact_relative_path=artifact_relative,
        artifact_sha256=sha256_bytes(artifact_payload),
        artifact_bytes=artifact_payload,
    )


__all__ = [
    "BACKEND_ROOT",
    "CHAPTER_ORDINALS",
    "LEGACY_HEAD",
    "LEGACY_REVISION",
    "LEGACY_TABLES",
    "MIGRATIONS_DIR",
    "PROJECT_ID",
    "PROJECT_TITLE",
    "LegacySeed",
    "alembic_config",
    "build_legacy_data_root",
    "chapter_id",
    "integrity",
    "row_counts",
    "run_id",
    "sha256_bytes",
    "sqlite_content_sha256",
    "table_names",
    "upgrade",
]
