"""R01 rehearsal on a real data root: clean install, legacy upgrade, failed migration.

Every scenario runs the real alembic chain in a temporary data root with the real
application ORM and the real read routes. No cloud call, no model download, and no
write to the studio data root.

What each drill proves:

1. clean install - upgrading from an *empty* data root reaches head and produces every
   table the ORM metadata declares;
2. legacy upgrade - a database parked at revision 0016 (two migrations behind head)
   keeps every row, stays readable through the ORM and through the real read routes,
   and gains the new tables *empty* instead of broken;
3. failed migration - a migration that raises mid-way does **not** advance the
   revision, and the state is *detected* (MIGRATION_INCOMPLETE) instead of being
   silently accepted. The drill also records the honest SQLite limitation: the backend
   reports "non-transactional DDL", so objects created before the failure can survive
   the rollback - which is exactly why the sanctioned recovery is restoring the
   verified pre-migration backup (see test_backup_rollback_drill.py) and not
   "run it again".
"""

from __future__ import annotations

from pathlib import Path
import shutil

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError

from app.contracts import JobKind, JobStatus, RunStatus
from app.db import models  # noqa: F401  (registers every ORM table on Base.metadata)
from app.db.base import Base, create_engine_for, session_factory
from app.db.migration_status import (
    MIGRATION_INCOMPLETE,
    head_revision,
    migration_count,
    migration_status,
    script_directory,
)
from app.db.models import (
    Chapter,
    Job,
    Project,
    TranslationRun,
    TranslationSegment,
    VoicePreviewJob,
)
from app.main import create_app
from app.settings.config import Settings
from tests.fixtures.legacy_data_root import (
    LEGACY_HEAD,
    LEGACY_REVISION,
    MIGRATIONS_DIR,
    PROJECT_ID,
    PROJECT_TITLE,
    build_legacy_data_root,
    chapter_id,
    integrity,
    row_counts,
    run_id,
    table_names,
    upgrade,
)


LOOPBACK_ORIGIN = "http://127.0.0.1:8765"


def _add_job(engine: Engine, *, job_id: str, kind: str, idempotency_key: str) -> None:
    """Insert one job through the ORM; a kind outside the whitelist raises."""
    with session_factory(engine)() as session:
        session.add(
            Job(
                id=job_id,
                kind=kind,
                status=JobStatus.QUEUED.value,
                project_id=PROJECT_ID,
                idempotency_key=idempotency_key,
            )
        )
        session.commit()


def test_clean_install_from_empty_data_root_reaches_head(tmp_path: Path) -> None:
    """A brand-new data root: upgrade head must build exactly the ORM schema."""
    data_root = tmp_path / "clean-data"
    data_root.mkdir()
    db_path = data_root / "studio.sqlite3"
    assert not db_path.exists()

    upgrade(db_path, "head")

    status = migration_status(db_path)
    assert status.in_sync, status.summary()
    assert status.database_revision == head_revision()
    # The chain length is derived, never frozen at a literal: every additive
    # migration (0019 memory_indexes is the newest) would otherwise break the drill
    # for a reason that has nothing to do with a clean install.
    assert status.migration_count == migration_count() >= 19
    assert integrity(db_path) == "ok"

    tables = table_names(db_path)
    missing = sorted(set(Base.metadata.tables) - tables)
    assert missing == [], f"clean install is missing ORM tables: {missing}"
    # The newest (additive) migrations are part of a clean install too.
    assert "voice_preview_jobs" in tables
    assert "memory_indexes" in tables
    assert "alembic_version" in tables

    print(
        f"EVIDENCE clean-install revision={status.database_revision} "
        f"migrations={status.migration_count} tables={len(tables)} integrity=ok"
    )


def test_upgrade_from_legacy_revision_preserves_rows_and_leaves_new_tables_empty(
    tmp_path: Path,
) -> None:
    """Legacy fixture at 0016 -> head: rows survive, new tables exist and are empty."""
    seed = build_legacy_data_root(tmp_path / "legacy-data")
    before = seed.counts
    assert all(count > 0 for count in before.values()), before
    assert "voice_preview_jobs" not in table_names(seed.db_path)

    upgrade(seed.db_path, "head")

    after = row_counts(seed.db_path)
    assert after == before
    status = migration_status(seed.db_path)
    # Upgrading a legacy root lands on whatever the chain head is now, which is
    # deliberately not the frozen legacy anchor (LEGACY_HEAD stays an explicitly
    # *old* schema - see tests/fixtures/legacy_data_root.py).
    assert status.database_revision == head_revision()
    assert status.in_sync, status.summary()
    assert integrity(seed.db_path) == "ok"

    assert "voice_preview_jobs" in table_names(seed.db_path)
    preview_engine = create_engine_for(seed.db_path)
    try:
        with session_factory(preview_engine)() as session:
            assert session.scalar(select(func.count()).select_from(VoicePreviewJob)) == 0

        # 0018 rewrote the jobs.kind whitelist: SUMMARIZE is now claimable and a bogus
        # kind is still rejected, which proves the rebuilt constraint is in place.
        with pytest.raises(IntegrityError):
            _add_job(
                preview_engine,
                job_id="99999999-9999-7999-8999-999999999999",
                kind="BOGUS",
                idempotency_key="rehearsal-bogus",
            )
        _add_job(
            preview_engine,
            job_id="88888888-8888-7888-8888-888888888888",
            kind=JobKind.SUMMARIZE.value,
            idempotency_key="rehearsal-summarize",
        )
        with session_factory(preview_engine)() as session:
            stored = session.get(Job, "88888888-8888-7888-8888-888888888888")
            assert stored is not None and stored.kind == "SUMMARIZE"
    finally:
        preview_engine.dispose()

    # Real ORM read-back of the legacy rows through the upgraded schema.
    engine = create_engine_for(seed.db_path)
    try:
        with session_factory(engine)() as session:
            project = session.get(Project, PROJECT_ID)
            assert project is not None and project.title == PROJECT_TITLE
            chapters = session.query(Chapter).filter_by(project_id=project.id).all()
            assert [chapter.ordinal for chapter in chapters] == [1, 2, 3]
            run = session.get(TranslationRun, run_id(1))
            assert run is not None and run.status == RunStatus.APPROVED.value
            targets = session.query(TranslationSegment).filter_by(translation_run_id=run.id).all()
            assert sorted(segment.target_text for segment in targets) == [
                "ban dich 1.1",
                "ban dich 1.2",
            ]
    finally:
        engine.dispose()

    assert seed.artifact_file.read_bytes() == seed.artifact_bytes

    print(
        f"EVIDENCE legacy-upgrade from={LEGACY_REVISION} to={status.database_revision} "
        f"rows_before={before} rows_after={after} new_table_rows=0 integrity=ok"
    )


def test_upgraded_legacy_data_root_is_readable_through_real_read_routes(tmp_path: Path) -> None:
    """The read routes keep serving pre-upgrade data (legacy read path evidence)."""
    seed = build_legacy_data_root(tmp_path / "legacy-served")
    upgrade(seed.db_path, "head")

    app = create_app(settings=Settings(data_root=seed.data_root), acquire_lock=False)
    with TestClient(app, base_url=LOOPBACK_ORIGIN) as client:
        listing = client.get("/api/projects")
        assert listing.status_code == 200
        legacy = next(
            (item for item in listing.json()["projects"] if item["id"] == PROJECT_ID),
            None,
        )
        assert legacy is not None, listing.json()
        assert legacy["title"] == PROJECT_TITLE
        assert legacy["chapterCount"] == 3

        detail = client.get(f"/api/projects/{PROJECT_ID}")
        assert detail.status_code == 200
        assert detail.json()["chapterCount"] == 3

        chapters = client.get(f"/api/projects/{PROJECT_ID}/chapters?limit=25")
        assert chapters.status_code == 200
        body = chapters.json()
        assert body["total"] == 3
        assert [item["ordinal"] for item in body["items"]] == [1, 2, 3]

        translation = client.get(f"/api/chapters/{chapter_id(1)}/translation")
        assert translation.status_code == 200, translation.text
        translated = translation.json()
        assert translated["run"]["status"] == RunStatus.APPROVED.value
        assert len(translated["segments"]) == 2

    print(
        "EVIDENCE legacy-read-routes GET /api/projects, /api/projects/{id}, "
        "/api/projects/{id}/chapters, /api/chapters/{id}/translation -> 200"
    )


#: A revision that creates DDL, writes a row and then raises mid-way. It is written
#: into a *copy* of the real migration chain, never into the repository. Its id and
#: down_revision are filled in from the real chain: the drill must sit exactly one
#: step above head, because a frozen literal would collide with a real additive
#: migration (0019 memory_indexes, X06) and leave two revisions on the same parent,
#: which alembic refuses to resolve.
BROKEN_REVISION_SOURCE = """
from alembic import op
import sqlalchemy as sa


revision = "{revision}"
down_revision = "{down_revision}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rehearsal_half_state",
        sa.Column("id", sa.String(length=36), primary_key=True),
    )
    op.execute(sa.text("INSERT INTO rehearsal_half_state (id) VALUES ('half')"))
    raise RuntimeError("REHEARSAL_FORCED_FAILURE")


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE rehearsal_half_state"))
"""


def rehearsal_revision_after(head: str) -> str:
    """The revision id one step above 'head' (the real chain says '0019' -> '0020')."""
    if not head.isdigit() or len(head) != 4:
        raise RuntimeError(f"REHEARSAL_REVISION_UNSUPPORTED_HEAD:{head}")
    return f"{int(head) + 1:04d}"


def _broken_migrations(tmp_path: Path) -> tuple[Path, str]:
    """A copy of the real chain plus one failing revision directly above head."""
    script_location = tmp_path / "broken-migrations"
    shutil.copytree(
        MIGRATIONS_DIR,
        script_location,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    down_revision = head_revision()
    revision = rehearsal_revision_after(down_revision)
    (script_location / "versions" / f"{revision}_rehearsal_broken.py").write_text(
        BROKEN_REVISION_SOURCE.format(revision=revision, down_revision=down_revision),
        encoding="utf-8",
    )
    return script_location, revision


def test_failed_migration_does_not_advance_revision_and_is_detected(tmp_path: Path) -> None:
    """A migration that raises mid-way must not look like a successful upgrade."""
    seed = build_legacy_data_root(tmp_path / "fail-data", revision=LEGACY_HEAD)
    before = seed.counts
    script_location, rehearsal_revision = _broken_migrations(tmp_path)
    real_head = head_revision()

    # Intent guard, asserted instead of assumed: the rehearsal revision is the
    # *only* child of the real head (no sibling on the same down_revision, so the
    # copied chain has one unambiguous head) and it is that chain's head.
    children = [
        revision.revision
        for revision in script_directory(script_location).walk_revisions()
        if revision.down_revision == real_head
    ]
    assert children == [rehearsal_revision]
    assert head_revision(script_location) == rehearsal_revision

    with pytest.raises(RuntimeError, match="REHEARSAL_FORCED_FAILURE"):
        upgrade(seed.db_path, "head", script_location)

    status = migration_status(seed.db_path, script_location=script_location)
    assert status.head_revision == rehearsal_revision
    # The failing revision did not advance the version row: what remains is the last
    # revision that succeeded (the real chain head, applied just before the broken
    # one). That is the honest form of 'the revision does not advance' now that the
    # rehearsal sits one step above a real additive migration instead of directly
    # above the legacy anchor.
    assert status.database_revision == real_head
    assert status.database_revision != rehearsal_revision
    assert status.detail == MIGRATION_INCOMPLETE
    assert status.in_sync is False
    assert integrity(seed.db_path) == "ok"
    assert row_counts(seed.db_path) == before

    # Honest SQLite limitation, reported by the backend itself:
    # "Will assume non-transactional DDL". The version row rolls back, DDL may not.
    assert "rehearsal_half_state" in table_names(seed.db_path)

    # Therefore a plain retry is NOT a recovery path: the leftover object makes the
    # same migration fail again instead of silently succeeding.
    with pytest.raises(Exception, match="already exists"):
        upgrade(seed.db_path, "head", script_location)

    # The state stays loudly out of sync until someone acts on it. Recovery is the
    # verified pre-migration backup (storage rollback drill) or a fixed forward
    # migration - never "run it again and hope".
    assert migration_status(seed.db_path, script_location=script_location).detail == MIGRATION_INCOMPLETE

    print(
        f"EVIDENCE fail-migration revision_stays={status.database_revision} "
        f"head={status.head_revision} status={status.detail} "
        f"rows_intact={row_counts(seed.db_path) == before} "
        "leftover_ddl=rehearsal_half_state retry_rejected=already exists"
    )
