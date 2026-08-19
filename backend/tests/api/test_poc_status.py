from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.poc import create_poc_router


def _client(data_root: Path) -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_poc_router(data_root))
    return TestClient(app)


def test_poc_status_is_not_run_without_report(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/poc/status")

    assert response.status_code == 200
    assert response.json() == {"status": "not_run"}


def test_poc_status_returns_ready_for_latest_verified_report_without_source_or_paths(tmp_path: Path) -> None:
    report_dir = tmp_path / "projects" / "poc"
    report_dir.mkdir(parents=True)
    report = report_dir / "poc-report-20260819T040000Z.json"
    payload = (
        b'{"schema_version":"truyenaudio-studio.poc-report.v1","generated_at":"2026-08-19T04:00:00+00:00",'
        b'"provider":"fake","gates":{"overall":{"passed":true}}}'
    )
    digest = hashlib.sha256(payload).hexdigest()
    report.write_bytes(payload)
    report.with_suffix(".json.sha256").write_text(f"{digest}  {report.name}\n", encoding="utf-8")

    response = _client(tmp_path).get("/api/poc/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["report_sha256"] == digest
    assert body["generated_at"] == "2026-08-19T04:00:00+00:00"
    assert body["gates"]["overall"]["passed"] is True
    assert str(tmp_path) not in response.text
    assert "source_text" not in response.text
    assert "target_text" not in response.text


def test_poc_status_returns_invalid_when_checksum_mismatches(tmp_path: Path) -> None:
    report_dir = tmp_path / "projects" / "poc"
    report_dir.mkdir(parents=True)
    report = report_dir / "poc-report-20260819T040000Z.json"
    report.write_text('{"schema_version":"truyenaudio-studio.poc-report.v1"}', encoding="utf-8")
    report.with_suffix(".json.sha256").write_text(f"{'0' * 64}  {report.name}\n", encoding="utf-8")

    response = _client(tmp_path).get("/api/poc/status")

    assert response.status_code == 200
    assert response.json()["status"] == "invalid"
