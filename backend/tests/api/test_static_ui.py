from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_root_serves_built_dashboard(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        '<!doctype html><div id="root">Truyen Audio Studio dashboard</div>',
        encoding="utf-8",
    )

    with TestClient(create_app(frontend_dist=dist, acquire_lock=False)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Truyen Audio Studio dashboard" in response.text


def test_spa_fallback_serves_dashboard_for_frontend_routes(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<div id="root">dashboard</div>', encoding="utf-8")

    with TestClient(create_app(frontend_dist=dist, acquire_lock=False)) as client:
        response = client.get("/projects/new")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "dashboard" in response.text
