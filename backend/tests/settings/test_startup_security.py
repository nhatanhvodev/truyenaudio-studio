from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.settings.config import Settings
from app.settings.startup_lock import AlreadyRunning, StartupLock
from app.main import app


def test_rejects_non_loopback(monkeypatch):
    monkeypatch.setenv("STUDIO_HOST", "0.0.0.0")

    with pytest.raises(ValueError, match="loopback"):
        Settings()


@pytest.mark.parametrize(
    ("env_name", "value", "message"),
    [
        ("STUDIO_PORT", "8766", "8765"),
        ("STUDIO_WORKER_CONCURRENCY", "2", "1"),
    ],
)
def test_rejects_non_local_runtime_settings(monkeypatch, env_name, value, message):
    monkeypatch.setenv(env_name, value)

    with pytest.raises(ValueError, match=message):
        Settings()


def test_second_process_lock_is_rejected(tmp_path):
    lock_path = tmp_path / "api.lock"

    with StartupLock(lock_path):
        with pytest.raises(AlreadyRunning):
            StartupLock(lock_path).acquire()


def test_release_is_idempotent_and_unlocks_file(tmp_path):
    lock_path = tmp_path / "api.lock"
    lock = StartupLock(lock_path)

    lock.acquire()
    lock.release()
    lock.release()

    with StartupLock(lock_path):
        pass

    assert lock_path.read_text(encoding="utf-8").strip()


def test_health_live_returns_live_status():
    with TestClient(app) as client:
        response = client.get("/api/health/live")

        assert response.status_code == 200
        assert response.json() == {"status": "live"}


def test_health_ready_returns_ready_status():
    with TestClient(app) as client:
        response = client.get("/api/health/ready")

        assert response.status_code == 200
        assert response.json() == {"status": "ready"}


def test_settings_uses_required_local_defaults(monkeypatch):
    monkeypatch.delenv("STUDIO_HOST", raising=False)
    monkeypatch.delenv("STUDIO_PORT", raising=False)
    monkeypatch.delenv("STUDIO_WORKER_CONCURRENCY", raising=False)
    monkeypatch.delenv("STUDIO_DATA_ROOT", raising=False)

    settings = Settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8765
    assert settings.worker_concurrency == 1
    assert settings.data_root == Path(r"D:\truyenaudio-studio\data")
