from __future__ import annotations

from pathlib import Path


def test_run_studio_applies_alembic_migrations_before_starting_api() -> None:
    repo_root = Path(__file__).parents[3]
    launcher = (repo_root / "run-studio.bat").read_text(encoding="utf-8")

    assert "scripts\\migrate.ps1" in launcher
    assert launcher.index("scripts\\migrate.ps1") < launcher.index("uvicorn")


def test_migration_script_upgrades_local_sqlite_to_head() -> None:
    repo_root = Path(__file__).parents[3]
    script = (repo_root / "scripts" / "migrate.ps1").read_text(encoding="utf-8")

    assert "command.upgrade" in script
    assert '"head"' in script
    assert "studio.sqlite3" in script
