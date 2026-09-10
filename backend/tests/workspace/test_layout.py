from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.api.workspace import create_workspace_router
from app.contracts import RightsStatus, SourceType
from app.db.models import Project
from app.modules.workspace.layout import (
    MAX_TABS_PER_PANE,
    LayoutConflict,
    load_layout,
    sanitize_layout,
    save_layout,
)
from app.settings.config import Settings

PROJECT_ID = "018f0000-0000-7000-8000-000000000901"
OTHER_PROJECT_ID = "018f0000-0000-7000-8000-000000000902"


def _seed(session, project_id: str = PROJECT_ID, slug: str = "layout-a") -> None:
    session.add(
        Project(
            id=project_id,
            title="Layout",
            slug=slug,
            source_type=SourceType.SELF_AUTHORED.value,
            rights_status=RightsStatus.PRIVATE_ONLY.value,
            default_language="zh-CN",
            target_language="vi-VN",
        )
    )
    session.commit()


def _tab(tab_id: str, project_id: str = PROJECT_ID, **overrides: object) -> dict[str, object]:
    return {
        "id": tab_id,
        "kind": "EDITOR",
        "projectId": project_id,
        "chapterId": f"chapter-{tab_id}",
        "title": f"Tab {tab_id}",
        "dirty": False,
        **overrides,
    }


def _layout(project_id: str = PROJECT_ID, tabs: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "version": 1,
        "projectId": project_id,
        "docked": True,
        "focus": "secondary",
        "panes": {
            "primary": {
                "tabs": tabs if tabs is not None else [_tab("t0", project_id)],
                "activeTabId": "t0",
            },
            "secondary": {"tabs": [], "activeTabId": None},
        },
    }


def test_save_and_restore_layout_round_trip(db_session) -> None:
    _seed(db_session)

    empty = load_layout(db_session, PROJECT_ID)
    assert empty.layout is None
    assert empty.revision is None
    assert empty.migrated is False

    saved = save_layout(db_session, PROJECT_ID, _layout(), expected_revision=None)

    assert saved.revision == 1
    assert saved.layout_version == 1
    restored = load_layout(db_session, PROJECT_ID)
    assert restored.layout == saved.layout
    assert restored.layout["docked"] is True
    assert restored.layout["focus"] == "secondary"


def test_save_layout_compare_and_swap_rejects_stale_revision(db_session) -> None:
    _seed(db_session)
    save_layout(db_session, PROJECT_ID, _layout(), expected_revision=None)

    with pytest.raises(LayoutConflict) as conflict:
        save_layout(db_session, PROJECT_ID, _layout(), expected_revision=7)
    assert conflict.value.code == "LAYOUT_REVISION_CONFLICT"

    # Nothing was overwritten and the revision did not move.
    assert load_layout(db_session, PROJECT_ID).revision == 1

    updated = save_layout(db_session, PROJECT_ID, _layout(), expected_revision=1)
    assert updated.revision == 2


def test_save_layout_rejects_missing_project_and_invalid_payload(db_session) -> None:
    with pytest.raises(ValueError) as missing:
        save_layout(db_session, PROJECT_ID, _layout(), expected_revision=None)
    assert str(missing.value) == "PROJECT_NOT_FOUND"

    _seed(db_session)
    with pytest.raises(ValueError) as invalid:
        save_layout(db_session, PROJECT_ID, ["not", "a", "layout"], expected_revision=None)
    assert str(invalid.value) == "LAYOUT_INVALID"

    with pytest.raises(ValueError) as incomplete:
        save_layout(db_session, PROJECT_ID, {"version": 1, "panes": {}}, expected_revision=None)
    assert str(incomplete.value) == "LAYOUT_INVALID"


def test_load_layout_unknown_project_raises(db_session) -> None:
    with pytest.raises(ValueError) as missing:
        load_layout(db_session, PROJECT_ID)
    assert str(missing.value) == "PROJECT_NOT_FOUND"


def test_sanitize_drops_foreign_tabs_and_enforces_the_pane_limit() -> None:
    tabs = [_tab(f"t{index}") for index in range(10)] + [_tab("foreign", OTHER_PROJECT_ID)]
    layout = _layout(tabs=tabs)
    layout["panes"]["primary"]["activeTabId"] = "foreign"  # type: ignore[index]

    sanitized = sanitize_layout(layout, PROJECT_ID)
    primary = sanitized["panes"]["primary"]  # type: ignore[index]

    assert len(primary["tabs"]) == MAX_TABS_PER_PANE
    assert all(tab["projectId"] == PROJECT_ID for tab in primary["tabs"])
    # The active id pointed at a dropped tab, so it falls back to the first tab.
    assert primary["activeTabId"] == "t0"


def test_empty_layout_is_accepted_and_has_no_active_tab(db_session) -> None:
    _seed(db_session)
    empty_panes = {
        "version": 1,
        "projectId": PROJECT_ID,
        "panes": {
            "primary": {"tabs": [], "activeTabId": None},
            "secondary": {"tabs": [], "activeTabId": None},
        },
    }

    saved = save_layout(db_session, PROJECT_ID, empty_panes, expected_revision=None)

    assert saved.layout["panes"]["primary"]["tabs"] == []  # type: ignore[index]
    assert saved.layout["panes"]["primary"]["activeTabId"] is None  # type: ignore[index]
    assert saved.layout["docked"] is False


def test_unknown_stored_layout_version_is_reported_as_migrated(db_session) -> None:
    _seed(db_session)
    save_layout(db_session, PROJECT_ID, _layout(), expected_revision=None)
    with db_session.get_bind().begin() as connection:
        connection.execute(
            text("UPDATE workspace_layouts SET layout_version = 99 WHERE project_id = :id"),
            {"id": PROJECT_ID},
        )
    db_session.expire_all()

    view = load_layout(db_session, PROJECT_ID)

    assert view.layout is None
    assert view.layout_version == 99
    assert view.migrated is True
    # A follow-up save writes the current version again.
    assert save_layout(db_session, PROJECT_ID, _layout(), expected_revision=view.revision).layout_version == 1


def test_workspace_layout_api_round_trip_and_errors(migrated_engine: Engine) -> None:
    db_path = Path(migrated_engine.url.database)
    from app.db.base import session_factory

    session = session_factory(migrated_engine)()
    try:
        _seed(session)
    finally:
        session.close()

    app = FastAPI()
    app.include_router(create_workspace_router(Settings(data_root=db_path.parent)))

    with TestClient(app) as client:
        empty = client.get(f"/api/projects/{PROJECT_ID}/workspace-layout")
        assert empty.status_code == 200
        assert empty.json() == {
            "projectId": PROJECT_ID,
            "layout": None,
            "layoutVersion": None,
            "revision": None,
            "migrated": False,
        }

        created = client.put(
            f"/api/projects/{PROJECT_ID}/workspace-layout",
            json={"layout": _layout(), "expected_revision": None},
        )
        assert created.status_code == 200
        assert created.json()["revision"] == 1

        restored = client.get(f"/api/projects/{PROJECT_ID}/workspace-layout")
        assert restored.json()["layout"]["panes"]["primary"]["tabs"][0]["id"] == "t0"

        conflict = client.put(
            f"/api/projects/{PROJECT_ID}/workspace-layout",
            json={"layout": _layout(), "expected_revision": 5},
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "LAYOUT_REVISION_CONFLICT"

        # A non-object payload is rejected by the request model (FastAPI 422)...
        wrong_type = client.put(
            f"/api/projects/{PROJECT_ID}/workspace-layout",
            json={"layout": "nope", "expected_revision": 1},
        )
        assert wrong_type.status_code == 422

        # ...while an object that is not a layout reaches the service and is a 400.
        invalid = client.put(
            f"/api/projects/{PROJECT_ID}/workspace-layout",
            json={"layout": {"version": 1, "panes": {}}, "expected_revision": 1},
        )
        assert invalid.status_code == 400
        assert invalid.json()["detail"] == "LAYOUT_INVALID"

        missing = client.get(f"/api/projects/{OTHER_PROJECT_ID}/workspace-layout")
        assert missing.status_code == 404

        # A foreign tab can never be stored for this project.
        mixed = _layout(tabs=[_tab("own"), _tab("other", OTHER_PROJECT_ID)])
        stored = client.put(
            f"/api/projects/{PROJECT_ID}/workspace-layout",
            json={"layout": mixed, "expected_revision": 1},
        )
        tabs = stored.json()["layout"]["panes"]["primary"]["tabs"]
        assert [tab["id"] for tab in tabs] == ["own"]


def test_layout_table_exists_in_the_migrated_schema(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        columns = {
            row[1] for row in connection.execute(text("PRAGMA table_info(workspace_layouts)")).all()
        }
    assert {
        "id",
        "project_id",
        "layout_version",
        "layout_json",
        "revision",
        "created_at",
        "updated_at",
    } <= columns
