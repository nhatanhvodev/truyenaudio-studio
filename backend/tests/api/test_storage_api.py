from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_storage_disk_route_is_registered(settings) -> None:
    with TestClient(create_app(settings=settings, acquire_lock=False)) as client:
        response = client.get("/api/storage/disk", params={"estimatedBytes": 1024})

    assert response.status_code == 200
    body = response.json()
    assert body["estimatedBytes"] == 1024
    assert body["level"] in {"ok", "warning", "hard"}
