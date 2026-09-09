from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.styles import create_styles_router
from app.contracts import RightsStatus, SourceType
from app.db.base import create_engine_for, session_factory
from app.db.models import Project, TranslationStyle
from app.modules.translation.style_profile import (
    ALLOWED_GENRES,
    ALLOWED_TONES,
    PRESETS,
    StyleCommand,
    StyleProfileService,
    active_styles,
)
from app.settings.config import Settings


def test_style_create_revision_and_semantic_edit_supersedes(db_session) -> None:
    project = _project(db_session, slug="style-rev")
    service = StyleProfileService(db_session, id_factory=_ids())

    first = service.upsert(
        project.id,
        StyleCommand("Web Novel", "webnovel", "natural", user_instruction="Dịch tự nhiên."),
    )
    assert first.style.revision_no == 1
    assert first.style.supersedes_id is None

    noop = service.upsert(
        project.id,
        StyleCommand("Web Novel", "webnovel", "natural", user_instruction="Dịch tự nhiên."),
    )
    assert noop.style.id == first.style.id
    assert noop.sha256 == first.sha256

    second = service.upsert(
        project.id,
        StyleCommand("Web Novel", "webnovel", "literary", user_instruction="Dịch văn chương."),
    )
    rows = db_session.scalars(
        select(TranslationStyle).order_by(TranslationStyle.revision_no)
    ).all()
    assert len(rows) == 2
    assert second.style.revision_no == 2
    assert second.style.supersedes_id == first.style.id
    assert second.sha256 != first.sha256
    assert [style.id for style in active_styles(db_session, project.id)] == [second.style.id]


def test_style_presets_are_valid_combinations_and_two_styles_coexist(db_session) -> None:
    project = _project(db_session, slug="style-presets")
    service = StyleProfileService(db_session, id_factory=_ids())

    assert PRESETS
    for preset in PRESETS:
        assert preset.genre in ALLOWED_GENRES
        assert preset.tone in ALLOWED_TONES
        assert preset.name and preset.key

    service.upsert(project.id, StyleCommand("Sát nghĩa", "general", "faithful"))
    service.upsert(project.id, StyleCommand("Văn học", "general", "literary", source_language="zh-CN", target_language="vi-VN"))

    names = {style.name for style in active_styles(db_session, project.id)}
    assert names == {"Sát nghĩa", "Văn học"}


def test_style_validation_rejects_unknown_genre_tone_and_missing_name(db_session) -> None:
    project = _project(db_session, slug="style-validate")
    service = StyleProfileService(db_session, id_factory=_ids())

    try:
        service.upsert(project.id, StyleCommand("X", "sci-fi", "natural"))
        raise AssertionError("expected genre rejection")
    except ValueError as exc:
        assert str(exc) == "STYLE_GENRE_INVALID:sci-fi"

    try:
        service.upsert(project.id, StyleCommand("X", "general", "harsh"))
        raise AssertionError("expected tone rejection")
    except ValueError as exc:
        assert str(exc) == "STYLE_TONE_INVALID:harsh"

    try:
        service.upsert(project.id, StyleCommand("   ", "general", "natural"))
        raise AssertionError("expected name rejection")
    except ValueError as exc:
        assert str(exc) == "STYLE_NAME_REQUIRED"


def test_styles_api_upsert_list_and_presets(tmp_path) -> None:
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "studio.sqlite3"
    backend_root = Path(__file__).parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    command.upgrade(config, "head")

    engine = create_engine_for(db_path)
    with session_factory(engine)() as session:
        project = _project(session, slug="style-api")
        session.commit()
        project_id = project.id
    engine.dispose()

    app = FastAPI()
    app.include_router(create_styles_router(Settings(data_root=tmp_path)))

    with TestClient(app) as client:
        presets = client.get(f"/api/projects/{project_id}/styles/presets")
        assert presets.status_code == 200
        assert any(item["key"] == "web-novel" for item in presets.json()["presets"])

        created = client.post(
            f"/api/projects/{project_id}/styles",
            json={
                "name": "Xianxia",
                "genre": "xianxia",
                "tone": "literary",
                "source_language": "zh-CN",
                "target_language": "vi-VN",
                "user_instruction": "Giữ tên nhân vật.",
            },
        )
        assert created.status_code == 200
        body = created.json()
        assert body["style"]["revision_no"] == 1
        assert body["style"]["name"] == "Xianxia"

        listed = client.get(f"/api/projects/{project_id}/styles")
        assert listed.status_code == 200
        assert [item["name"] for item in listed.json()["styles"]] == ["Xianxia"]

    engine = create_engine_for(db_path)
    with session_factory(engine)() as session:
        assert session.scalar(select(TranslationStyle.id).limit(1)) is not None
    engine.dispose()


def _project(db_session, slug: str) -> Project:
    project = Project(
        id="018f0000-0000-7000-8000-000000000202",
        title="Truyen",
        slug=slug,
        source_type=SourceType.USER_SUPPLIED_PRIVATE.value,
        rights_status=RightsStatus.PRIVATE_ONLY.value,
        default_language="zh-CN",
        target_language="vi-VN",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _ids():
    counter = 0

    def factory() -> str:
        nonlocal counter
        counter += 1
        return f"018f0000-0000-7000-8000-{counter:012x}"

    return factory
