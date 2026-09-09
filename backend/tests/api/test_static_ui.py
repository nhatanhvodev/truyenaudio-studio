from pathlib import Path

from fastapi.testclient import TestClient
import pytest

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


@pytest.mark.parametrize(
    "path",
    (
        r"/api\health/ready",
        "/api%5Chealth%2Fready",
        "/api%255Chealth%252Fready",
        "/%61pi/health/ready",
        "/%2561pi%252Fhealth%252Fready",
        "/API/health/ready",
        "//api/health/ready",
        "/%2Fapi/health/ready",
        "/%252Fapi%252Fhealth%252Fready",
        "//API/health/ready",
    ),
)
def test_spa_fallback_never_serves_api_shaped_paths(tmp_path: Path, path: str) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<div id="root">dashboard</div>', encoding="utf-8")

    with TestClient(
        create_app(frontend_dist=dist, acquire_lock=False),
        base_url="http://127.0.0.1:8765",
    ) as client:
        target = f"http://127.0.0.1:8765{path}" if path.startswith("//") else path
        response = client.get(target)

    assert response.status_code == 404


def test_spa_fallback_still_serves_valid_frontend_routes(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<div id="root">dashboard</div>', encoding="utf-8")

    with TestClient(create_app(frontend_dist=dist, acquire_lock=False)) as client:
        response = client.get("/projects/new")

    assert response.status_code == 200
    assert "dashboard" in response.text
