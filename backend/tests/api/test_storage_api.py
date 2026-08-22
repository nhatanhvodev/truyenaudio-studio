from __future__ import annotations

from pathlib import Path
import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import create_app
from app.settings.config import Settings


def test_storage_disk_route_is_registered(settings) -> None:
    with TestClient(create_app(settings=settings, acquire_lock=False), base_url="http://127.0.0.1:8765") as client:
        response = client.get("/api/storage/disk", params={"estimatedBytes": 1024})

    assert response.status_code == 200
    body = response.json()
    assert body["estimatedBytes"] == 1024
    assert body["level"] in {"ok", "warning", "hard"}


def test_storage_restore_uses_project_artifact_root(migrated_engine, tmp_path: Path) -> None:
    payload = b"normal source bytes"
    relative_path = "projects/truyen/sources/chapter-0001/revision-0001.txt"
    artifact_path = tmp_path / relative_path
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(payload)
    _insert_artifact(
        migrated_engine,
        artifact_id="018f0000-0000-7000-8000-000000050001",
        kind="SOURCE_SNAPSHOT",
        relative_path=relative_path,
        payload=payload,
    )
    migrated_engine.dispose()
    settings = Settings(data_root=tmp_path)

    with TestClient(create_app(settings=settings, acquire_lock=False), base_url="http://127.0.0.1:8765") as client:
        token = client.get("/api/security/bootstrap").json()["csrfToken"]
        headers = {"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": token}
        backup_response = client.post("/api/storage/backups", headers=headers)
        assert backup_response.status_code == 200
        restore_response = client.post(f"/api/storage/backups/{backup_response.json()['id']}/restore", headers=headers)

    assert restore_response.status_code == 200
    assert restore_response.json()["artifactPointerCount"] == 1


def _insert_artifact(engine, *, artifact_id: str, kind: str, relative_path: str, payload: bytes) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO artifacts
                (id, kind, status, relative_path, sha256, byte_size, mime_type,
                 input_hash, settings_hash, created_at, updated_at)
                VALUES
                (:id, :kind, 'READY', :relative_path, :sha256, :byte_size, 'text/plain',
                 :input_hash, :settings_hash,
                 '2026-08-19T00:00:00+00:00',
                 '2026-08-19T00:00:00+00:00')
                """
            ),
            {
                "id": artifact_id,
                "kind": kind,
                "relative_path": relative_path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "byte_size": len(payload),
                "input_hash": "b" * 64,
                "settings_hash": "c" * 64,
            },
        )
