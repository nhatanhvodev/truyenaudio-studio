from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.modules.poc.report import build_fake_poc_report, write_poc_report


def test_fake_poc_report_is_deterministic_and_redacted() -> None:
    first = build_fake_poc_report()
    second = build_fake_poc_report()

    assert first == second
    assert first["schema_version"] == "truyenaudio-studio.poc-report.v1"
    assert first["provider"] == "fake"
    assert first["gates"]["overall"]["passed"] is True
    assert len(first["translation"]["scores"]) == 20
    assert first["translation"]["model_snapshot_hash"]
    assert first["translation"]["license_card_artifact"]["sha256"]
    assert first["translation"]["rate_card_snapshot"]["source_url"]
    assert "source_text" not in json.dumps(first)
    assert "target_text" not in json.dumps(first)


def test_write_poc_report_uses_artifact_store_semantics_and_checksum(tmp_path: Path) -> None:
    report = build_fake_poc_report()

    saved = write_poc_report(report, tmp_path)
    report_path = tmp_path / saved["relative_path"]
    checksum_path = Path(str(report_path) + ".sha256")

    payload = report_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    assert saved["sha256"] == digest
    assert checksum_path.read_text(encoding="utf-8") == f"{digest}  {report_path.name}\n"
    assert list(tmp_path.rglob("*.partial")) == []


def test_write_poc_report_is_repeatable_when_existing_report_matches(tmp_path: Path) -> None:
    report = build_fake_poc_report()

    first = write_poc_report(report, tmp_path)
    second = write_poc_report(report, tmp_path)

    assert second == first
    assert list(tmp_path.rglob("*.partial")) == []
