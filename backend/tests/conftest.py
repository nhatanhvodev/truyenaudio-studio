from __future__ import annotations

import socket
from pathlib import Path
from typing import Callable, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.settings.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_root=tmp_path)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    original_connect = socket.socket.connect

    def blocked_connect(self: socket.socket, address: object) -> None:
        if isinstance(address, tuple) and address and address[0] in {"127.0.0.1", "::1", "localhost"}:
            return original_connect(self, address)
        raise RuntimeError("external network access is blocked in tests")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)


@pytest.fixture
def deterministic_uuid7_factory() -> Callable[[], str]:
    counter = 0

    def new_id() -> str:
        nonlocal counter
        counter += 1
        return f"018f0000-0000-7000-8000-{counter:012x}"

    return new_id


@pytest.fixture
def migrated_engine(tmp_path: Path) -> Iterator[Engine]:
    from app.db.base import create_engine_for

    db_path = tmp_path / "studio.sqlite3"
    engine = create_engine_for(db_path)
    backend_root = Path(__file__).parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    command.upgrade(config, "head")

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    from app.db.base import session_factory

    factory = session_factory(migrated_engine)
    with factory() as session:
        yield session


@pytest.fixture
def artifact_store(tmp_path: Path):
    from app.modules.artifacts.store import ArtifactStore

    return ArtifactStore(tmp_path)
