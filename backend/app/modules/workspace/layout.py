"""Per-project workspace layout persistence (task U05 round 2).

The client owns the layout shape (see ``frontend/src/features/workspace/
workspaceLayout.ts``); the server stores it as an opaque versioned JSON document
scoped to one project and guards writes with compare-and-swap on ``revision``:

- read: returns the stored layout, or ``layout=None`` with ``migrated=True``
  when the stored ``layout_version`` is not understood — the client then falls
  back to its default layout instead of the workspace failing to load;
- write: ``expected_revision`` must match the stored revision (``None``/``0``
  when no layout exists yet), otherwise ``LayoutConflict`` and nothing is
  written;
- validation: the payload must look like a layout (two panes of tabs), tabs
  belonging to another project are dropped, at most 8 tabs per pane are kept,
  and the document is capped at 256 KiB — a foreign project's tabs can never be
  persisted into this project's workspace.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import new_id
from app.db.models import Project, WorkspaceLayout

SUPPORTED_LAYOUT_VERSION = 1
MAX_TABS_PER_PANE = 8
MAX_LAYOUT_BYTES = 262_144

PANE_IDS = ("primary", "secondary")


class LayoutConflict(Exception):
    def __init__(self, code: str = "LAYOUT_REVISION_CONFLICT") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LayoutView:
    project_id: str
    layout: dict[str, object] | None
    layout_version: int | None
    revision: int | None
    migrated: bool


def load_layout(session: Session, project_id: str) -> LayoutView:
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError("PROJECT_NOT_FOUND")
    row = session.scalar(select(WorkspaceLayout).where(WorkspaceLayout.project_id == project_id))
    if row is None:
        return LayoutView(project_id, None, None, None, False)
    if row.layout_version != SUPPORTED_LAYOUT_VERSION:
        # Unknown/newer format: the client restores its default layout instead.
        return LayoutView(project_id, None, row.layout_version, row.revision, True)
    return LayoutView(
        project_id,
        sanitize_layout(row.layout_json, project_id),
        row.layout_version,
        row.revision,
        False,
    )


def save_layout(
    session: Session,
    project_id: str,
    layout: object,
    *,
    expected_revision: int | None,
    id_factory: Callable[[], str] = new_id,
) -> LayoutView:
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError("PROJECT_NOT_FOUND")
    normalized = sanitize_layout(layout, project_id)
    _require_layout_shape(normalized, layout)

    row = session.scalar(select(WorkspaceLayout).where(WorkspaceLayout.project_id == project_id))
    if row is None:
        if expected_revision not in (None, 0):
            raise LayoutConflict()
        row = WorkspaceLayout(
            id=id_factory(),
            project_id=project_id,
            layout_version=SUPPORTED_LAYOUT_VERSION,
            layout_json=normalized,
            revision=1,
        )
        session.add(row)
    else:
        if expected_revision != row.revision:
            raise LayoutConflict()
        row.layout_version = SUPPORTED_LAYOUT_VERSION
        row.layout_json = normalized
        row.revision = row.revision + 1
    session.flush()
    session.commit()
    return LayoutView(project_id, normalized, row.layout_version, row.revision, False)


def sanitize_layout(layout: object, project_id: str) -> dict[str, object]:
    """Keep only this project's tabs and enforce the pane limit."""
    source = layout if isinstance(layout, dict) else {}
    panes = source.get("panes")
    panes = panes if isinstance(panes, dict) else {}
    sanitized_panes: dict[str, object] = {}
    for pane in PANE_IDS:
        sanitized_panes[pane] = _sanitize_pane(panes.get(pane), project_id)
    return {
        "version": SUPPORTED_LAYOUT_VERSION,
        "projectId": project_id,
        "panes": sanitized_panes,
        "docked": source.get("docked") is True,
        "focus": "secondary" if source.get("focus") == "secondary" else "primary",
    }


def _sanitize_pane(value: object, project_id: str) -> dict[str, object]:
    pane = value if isinstance(value, dict) else {}
    raw_tabs = pane.get("tabs")
    tabs: list[dict[str, object]] = []
    if isinstance(raw_tabs, list):
        for raw in raw_tabs:
            if not _is_tab(raw) or raw.get("projectId") != project_id:
                continue
            tabs.append(
                {
                    "id": raw["id"],
                    "kind": raw["kind"],
                    "projectId": project_id,
                    "chapterId": raw.get("chapterId"),
                    "title": raw["title"],
                    "dirty": raw.get("dirty") is True,
                }
            )
    tabs = tabs[:MAX_TABS_PER_PANE]
    active = pane.get("activeTabId")
    active_tab_id = active if isinstance(active, str) and any(tab["id"] == active for tab in tabs) else None
    if active_tab_id is None and tabs:
        active_tab_id = tabs[0]["id"]
    return {"tabs": tabs, "activeTabId": active_tab_id}


def _require_layout_shape(normalized: dict[str, object], original: object) -> None:
    if not isinstance(original, dict):
        raise ValueError("LAYOUT_INVALID")
    panes = original.get("panes")
    if not isinstance(panes, dict) or any(pane not in panes for pane in PANE_IDS):
        raise ValueError("LAYOUT_INVALID")
    encoded = json.dumps(normalized, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_LAYOUT_BYTES:
        raise ValueError("LAYOUT_TOO_LARGE")


def _is_tab(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        isinstance(value.get("id"), str)
        and isinstance(value.get("kind"), str)
        and isinstance(value.get("title"), str)
        and (value.get("chapterId") is None or isinstance(value.get("chapterId"), str))
    )
