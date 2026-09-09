from __future__ import annotations

import importlib.util
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.contracts import ArtifactKind, ArtifactStatus, CloudConsentStatus, ProviderKind, RightsStatus, SourceType
from app.modules.artifacts.store import ArtifactStore, ArtifactWrite


NOW = datetime(2026, 8, 19, 5, 0, 0, tzinfo=UTC)
PROJECT_ID = "018f0000-0000-7000-8000-000000007001"
PROFILE_ID = "018f0000-0000-7000-8000-000000007002"
AUTH_ID = "018f0000-0000-7000-8000-000000007003"
CONSENT_ID = "018f0000-0000-7000-8000-000000007004"
ARTIFACT_ID = "018f0000-0000-7000-8000-000000007005"


def _load_qwen_probe():
    path = Path(__file__).parents[3] / "scripts" / "poc" / "qwen_probe.py"
    spec = importlib.util.spec_from_file_location("qwen_probe_for_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_project(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO projects (id, title, slug, source_type, rights_status, created_at, updated_at)
                VALUES (:id, 'POC', 'poc', :source_type, :rights_status, :now, :now)
                """
            ),
            {
                "id": PROJECT_ID,
                "source_type": SourceType.SELF_AUTHORED.value,
                "rights_status": RightsStatus.PRIVATE_ONLY.value,
                "now": NOW.isoformat(),
            },
        )


def _seed_valid_gate(engine: Engine, data_root: Path) -> None:
    _seed_project(engine)
    store = ArtifactStore(data_root)
    with store.begin(
        ArtifactWrite(
            kind=ArtifactKind.LICENSE_SNAPSHOT,
            relative_path="projects/poc/qwen-policy.json",
            input_hash="1" * 64,
            settings_hash="2" * 64,
            mime_type="application/json",
        )
    ) as writer:
        writer.file.write(b'{"provider":"qwen","policy":"manual-poc"}')
        saved = writer.commit()

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO provider_profiles
                (id, provider_kind, adapter_name, display_name, model, region, enabled, created_at, updated_at)
                VALUES (:profile_id, :kind, 'qwen-mt', 'Qwen MT Flash', 'qwen-mt-flash', 'ap-southeast-1', 1, :now, :now)
                """
            ),
            {"profile_id": PROFILE_ID, "kind": ProviderKind.TRANSLATOR.value, "now": NOW.isoformat()},
        )
        connection.execute(
            text(
                """
                INSERT INTO artifacts
                (id, kind, status, relative_path, sha256, byte_size, mime_type, input_hash, settings_hash,
                 created_at, updated_at)
                VALUES (:artifact_id, :kind, :status, :relative_path, :sha256, :size, 'application/json',
                        :input_hash, :settings_hash, :now, :now)
                """
            ),
            {
                "artifact_id": ARTIFACT_ID,
                "kind": ArtifactKind.LICENSE_SNAPSHOT.value,
                "status": ArtifactStatus.READY.value,
                "relative_path": saved.relative_path,
                "sha256": saved.sha256,
                "size": saved.byte_size,
                "input_hash": "1" * 64,
                "settings_hash": "2" * 64,
                "now": NOW.isoformat(),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO budget_authorizations
                (id, operation_id, estimate_vnd, contingency_vnd, expires_at, status, created_at, updated_at)
                VALUES (:auth_id, 'poc-qwen', 1000, 150, :expires_at, 'HELD', :now, :now)
                """
            ),
            {"auth_id": AUTH_ID, "expires_at": (NOW + timedelta(minutes=5)).isoformat(), "now": NOW.isoformat()},
        )
        connection.execute(
            text(
                """
                INSERT INTO cloud_processing_consents
                (id, project_id, provider_profile_id, status, policy_snapshot_artifact_id, accepted_at,
                 created_at, updated_at)
                VALUES (:consent_id, :project_id, :profile_id, :status, :artifact_id, :now, :now, :now)
                """
            ),
            {
                "consent_id": CONSENT_ID,
                "project_id": PROJECT_ID,
                "profile_id": PROFILE_ID,
                "status": CloudConsentStatus.GRANTED.value,
                "artifact_id": ARTIFACT_ID,
                "now": NOW.isoformat(),
            },
        )


class RecordingHttp:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int) -> Any:
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _Response()


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {
            "output": {
                "choices": [{"message": {"role": "assistant", "content": "Ban dich mau"}}]
            },
            "usage": {"input_tokens": 10, "output_tokens": 8},
            "request_id": "poc-req-001",
        }


def test_qwen_probe_invalid_gate_returns_consent_required_before_http(migrated_engine: Engine, tmp_path: Path) -> None:
    _seed_project(migrated_engine)
    module = _load_qwen_probe()
    http = RecordingHttp()

    result = module.run_probe(
        database_path=tmp_path / "studio.sqlite3",
        data_root=tmp_path,
        project_id=PROJECT_ID,
        provider_profile_id=PROFILE_ID,
        authorization_id=AUTH_ID,
        cloud_consent_id=CONSENT_ID,
        operation_id="poc-qwen",
        api_key="redacted",
        http_client=http,
        now=NOW,
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "POC_CLOUD_CONSENT_REQUIRED"
    assert http.calls == []


@pytest.mark.parametrize(
    ("name", "payload", "setup_sql"),
    [
        ("empty", b"", None),
        ("wrong_schema", None, "CREATE TABLE unrelated(id TEXT)"),
        ("corrupt", b"not a sqlite database", None),
        ("missing_tables", None, "CREATE TABLE projects(id TEXT PRIMARY KEY)"),
    ],
)
def test_qwen_probe_database_failures_block_without_http_or_path_leak(
    tmp_path: Path,
    name: str,
    payload: bytes | None,
    setup_sql: str | None,
) -> None:
    database_path = tmp_path / f"{name}.sqlite3"
    if payload is not None:
        database_path.write_bytes(payload)
    if setup_sql is not None:
        with sqlite3.connect(database_path) as connection:
            connection.execute(setup_sql)
    if payload is None and setup_sql is None:
        database_path.touch()
    module = _load_qwen_probe()
    http = RecordingHttp()

    result = module.run_probe(
        database_path=database_path,
        data_root=tmp_path,
        project_id=PROJECT_ID,
        provider_profile_id=PROFILE_ID,
        authorization_id=AUTH_ID,
        cloud_consent_id=CONSENT_ID,
        operation_id="poc-qwen",
        api_key="redacted",
        http_client=http,
        now=NOW,
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "POC_CLOUD_CONSENT_REQUIRED"
    assert result["reason"] == "database_unavailable"
    assert http.calls == []
    assert str(tmp_path) not in str(result)


def test_qwen_probe_valid_gate_makes_exactly_one_redacted_batch_call(
    migrated_engine: Engine,
    tmp_path: Path,
) -> None:
    _seed_valid_gate(migrated_engine, tmp_path)
    module = _load_qwen_probe()
    http = RecordingHttp()

    result = module.run_probe(
        database_path=tmp_path / "studio.sqlite3",
        data_root=tmp_path,
        project_id=PROJECT_ID,
        provider_profile_id=PROFILE_ID,
        authorization_id=AUTH_ID,
        cloud_consent_id=CONSENT_ID,
        operation_id="poc-qwen",
        api_key="secret-value",
        http_client=http,
        now=NOW,
    )

    assert result["status"] == "ok"
    assert len(http.calls) == 1
    assert http.calls[0]["headers"]["Authorization"] == "Bearer secret-value"
    call_json = http.calls[0]["json"]
    assert call_json["input"]["messages"][0]["role"] == "user"
    assert call_json["input"]["messages"][0]["content"]
    assert call_json["parameters"]["translation_options"]["source_lang"] == "zh"
    assert call_json["parameters"]["translation_options"]["target_lang"] == "vi"
    assert "system" not in str(call_json)
    assert "source_text" not in str(result)
    assert "secret-value" not in str(result)
