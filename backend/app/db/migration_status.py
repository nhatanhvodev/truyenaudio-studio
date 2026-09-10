"""Migration-state introspection used by the R01 rollout gate.

One source of truth for the question *is this database at the head revision?*
It is used by:

- ``scripts/preflight.ps1 -VerifyDatabase`` - refuse to start the studio when the
  data root is not at head (for example after an interrupted upgrade);
- ``scripts/migrate.ps1`` - report the revision before/after an upgrade and fail
  when the upgrade did not actually land on head;
- ``backend/tests/db/test_migration_rehearsal.py`` - prove the failed-migration
  drill is *detected* rather than silently accepted.

Why a revision check and not a transaction: SQLite reports "non-transactional DDL"
to alembic, so a migration that raises mid-way can leave DDL objects behind even
though the version row rolls back (proved by the R01 fail-migration drill, see
docs/validation/release-gate.md). The version check is therefore the detection
mechanism, and the sanctioned recovery is restoring the verified pre-migration
backup - never "run it again and hope".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
MIGRATIONS_DIR = BACKEND_ROOT / "migrations"

IN_SYNC = "OK"
DATABASE_MISSING = "DATABASE_MISSING"
REVISION_MISSING = "REVISION_MISSING"
MIGRATION_INCOMPLETE = "MIGRATION_INCOMPLETE"


@dataclass(frozen=True)
class MigrationStatus:
    """Revision state of one SQLite data-root database."""

    database_path: Path
    database_exists: bool
    database_revision: str | None
    head_revision: str
    migration_count: int
    detail: str

    @property
    def in_sync(self) -> bool:
        return self.detail == IN_SYNC

    def summary(self) -> str:
        """One line for a shell script; never silent about a mismatch."""
        if not self.database_exists:
            return (
                f"{DATABASE_MISSING}: {self.database_path} does not exist "
                f"(head {self.head_revision}, {self.migration_count} migrations)"
            )
        return (
            f"revision={self.database_revision} head={self.head_revision} "
            f"migrations={self.migration_count} status={self.detail}"
        )


def script_directory(script_location: Path | str | None = None) -> ScriptDirectory:
    """Alembic script directory; no database connection is opened."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(script_location or MIGRATIONS_DIR))
    # ScriptDirectory only needs the URL to exist, it never connects.
    config.set_main_option("sqlalchemy.url", "sqlite:///unused.sqlite3")
    return ScriptDirectory.from_config(config)


def head_revision(script_location: Path | str | None = None) -> str:
    head = script_directory(script_location).get_current_head()
    if head is None:
        raise RuntimeError("MIGRATION_GRAPH_INVALID:no head revision")
    return str(head)


def migration_count(script_location: Path | str | None = None) -> int:
    """Number of revisions in the linear chain (base -> head)."""
    return sum(1 for _ in script_directory(script_location).walk_revisions())


def read_database_revision(db_path: Path | str) -> str | None:
    """Current alembic revision, or None when the database has no version yet."""
    path = Path(db_path)
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.DatabaseError:
        return None
    if row is None:
        return None
    return str(row[0])


def migration_status(
    db_path: Path | str,
    *,
    script_location: Path | str | None = None,
) -> MigrationStatus:
    """Classify the database as OK / DATABASE_MISSING / REVISION_MISSING / MIGRATION_INCOMPLETE."""
    path = Path(db_path)
    head = head_revision(script_location)
    count = migration_count(script_location)
    exists = path.is_file()
    revision = read_database_revision(path) if exists else None
    if not exists:
        detail = DATABASE_MISSING
    elif revision is None:
        detail = REVISION_MISSING
    elif revision == head:
        detail = IN_SYNC
    else:
        detail = MIGRATION_INCOMPLETE
    return MigrationStatus(
        database_path=path,
        database_exists=exists,
        database_revision=revision,
        head_revision=head,
        migration_count=count,
        detail=detail,
    )


__all__ = [
    "ALEMBIC_INI",
    "DATABASE_MISSING",
    "IN_SYNC",
    "MIGRATIONS_DIR",
    "MIGRATION_INCOMPLETE",
    "REVISION_MISSING",
    "MigrationStatus",
    "head_revision",
    "migration_count",
    "migration_status",
    "read_database_revision",
    "script_directory",
]
