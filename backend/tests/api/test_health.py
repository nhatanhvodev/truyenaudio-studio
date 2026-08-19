from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.health import ReadinessProbe, create_health_router


NOW = datetime(2026, 8, 19, 4, 0, 0, tzinfo=UTC)


def _client(probe: ReadinessProbe) -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_health_router(probe))
    return TestClient(app)


def _heartbeat(data_root: Path, seen_at: datetime = NOW) -> None:
    path = data_root / "worker-heartbeat.json"
    path.write_text(
        '{"schema_version":"truyenaudio-studio.worker-heartbeat.v1","status":"idle","updated_at":"'
        + seen_at.isoformat()
        + '"}',
        encoding="utf-8",
    )


def test_ready_returns_200_with_required_components(tmp_path: Path) -> None:
    _heartbeat(tmp_path)
    probe = ReadinessProbe(
        data_root=tmp_path,
        database_path=tmp_path / "studio.sqlite3",
        clock=lambda: NOW,
        executable_resolver=lambda name: f"C:/tools/{name}.exe",
        executable_probe=lambda path: True,
    )

    response = _client(probe).get("/api/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["components"]["providers"]["status"] == "info"
    assert "path" not in str(body).lower()


def test_ready_returns_503_when_worker_heartbeat_is_absent_or_stale(tmp_path: Path) -> None:
    missing = ReadinessProbe(
        data_root=tmp_path,
        database_path=tmp_path / "studio.sqlite3",
        clock=lambda: NOW,
        executable_resolver=lambda name: f"C:/tools/{name}.exe",
        executable_probe=lambda path: True,
    )
    assert _client(missing).get("/api/health/ready").status_code == 503

    _heartbeat(tmp_path, NOW - timedelta(seconds=91))
    stale = ReadinessProbe(
        data_root=tmp_path,
        database_path=tmp_path / "studio.sqlite3",
        clock=lambda: NOW,
        executable_resolver=lambda name: f"C:/tools/{name}.exe",
        executable_probe=lambda path: True,
    )

    response = _client(stale).get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["components"]["worker"]["status"] == "stale"


def test_ready_returns_503_for_missing_ffmpeg_without_leaking_paths(tmp_path: Path) -> None:
    _heartbeat(tmp_path)
    probe = ReadinessProbe(
        data_root=tmp_path,
        database_path=tmp_path / "studio.sqlite3",
        clock=lambda: NOW,
        executable_resolver=lambda name: None,
        executable_probe=lambda path: True,
    )

    response = _client(probe).get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["components"]["ffmpeg"]["status"] == "missing"
    assert str(tmp_path) not in response.text


def test_ready_returns_503_when_sqlite_write_probe_fails(tmp_path: Path) -> None:
    _heartbeat(tmp_path)
    probe = ReadinessProbe(
        data_root=tmp_path,
        database_path=tmp_path / "studio.sqlite3",
        clock=lambda: NOW,
        executable_resolver=lambda name: f"C:/tools/{name}.exe",
        executable_probe=lambda path: True,
        sqlite_probe=lambda: False,
    )

    response = _client(probe).get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["components"]["sqlite"]["status"] == "fail"


def test_readiness_temp_probe_leaves_no_files(tmp_path: Path) -> None:
    _heartbeat(tmp_path)
    probe = ReadinessProbe(
        data_root=tmp_path,
        database_path=tmp_path / "studio.sqlite3",
        clock=lambda: NOW,
        executable_resolver=lambda name: f"C:/tools/{name}.exe",
        executable_probe=lambda path: True,
    )

    _client(probe).get("/api/health/ready")

    assert list((tmp_path / "temp").glob("*")) == []


def test_ready_returns_503_when_ffmpeg_link_exists_but_cannot_run(tmp_path: Path) -> None:
    _heartbeat(tmp_path)
    probe = ReadinessProbe(
        data_root=tmp_path,
        database_path=tmp_path / "studio.sqlite3",
        clock=lambda: NOW,
        executable_resolver=lambda name: f"C:/tools/{name}.exe",
        executable_probe=lambda path: False,
    )

    response = _client(probe).get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["components"]["ffmpeg"]["status"] == "missing"
