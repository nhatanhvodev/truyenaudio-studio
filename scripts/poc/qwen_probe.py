from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


POC_CLOUD_CONSENT_REQUIRED = "POC_CLOUD_CONSENT_REQUIRED"


def run_probe(
    *,
    database_path: Path,
    data_root: Path,
    project_id: str,
    provider_profile_id: str,
    authorization_id: str,
    cloud_consent_id: str,
    operation_id: str,
    api_key: str,
    http_client: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    gate = _validate_gate(
        database_path=database_path,
        data_root=data_root,
        project_id=project_id,
        provider_profile_id=provider_profile_id,
        authorization_id=authorization_id,
        cloud_consent_id=cloud_consent_id,
        operation_id=operation_id,
        now=now,
    )
    if not gate["ok"]:
        return {"status": "blocked", "error_code": POC_CLOUD_CONSENT_REQUIRED, "reason": gate["reason"]}

    if http_client is None:
        import httpx

        http_client = httpx.Client()

    payload = {
        "segments": [{"segment_id": "seg-001", "source_sha256": hashlib.sha256(b"synthetic segment").hexdigest()}],
        "terms": [{"source": "ten rieng", "target": "ten rieng"}],
        "tm_list": [{"source_sha256": hashlib.sha256(b"old").hexdigest(), "target": "ban dich cu"}],
        "domain": "Vietnamese audiobook translation POC",
    }
    response = http_client.post(
        "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    translations = body.get("translations")
    usage = body.get("usage")
    if not isinstance(translations, list) or not translations:
        return {"status": "error", "error_code": "PROVIDER_SCHEMA", "reason": "missing translations"}
    if not isinstance(usage, dict):
        return {"status": "error", "error_code": "PROVIDER_SCHEMA", "reason": "missing usage"}

    return {
        "status": "ok",
        "provider": "qwen",
        "model": str(body.get("model", gate["model"])),
        "provider_version": str(body.get("provider_version", "unknown")),
        "usage": {
            "input_tokens": int(usage.get("input_tokens", 0)),
            "output_tokens": int(usage.get("output_tokens", 0)),
        },
        "latency_ms": 0,
        "cost_vnd": 0,
        "model_snapshot_hash": hashlib.sha256(str(body.get("model", gate["model"])).encode("utf-8")).hexdigest(),
    }


def _validate_gate(
    *,
    database_path: Path,
    data_root: Path,
    project_id: str,
    provider_profile_id: str,
    authorization_id: str,
    cloud_consent_id: str,
    operation_id: str,
    now: datetime,
) -> dict[str, Any]:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        with connection:
            authorization = connection.execute(
                """
                SELECT status, expires_at, operation_id
                FROM budget_authorizations
                WHERE id = ?
                """,
                (authorization_id,),
            ).fetchone()
            if authorization is None or authorization["status"] != "HELD":
                return {"ok": False, "reason": "authorization_missing_or_not_held"}
            if authorization["operation_id"] != operation_id:
                return {"ok": False, "reason": "authorization_operation_mismatch"}
            try:
                expires_at = datetime.fromisoformat(str(authorization["expires_at"]))
            except ValueError:
                return {"ok": False, "reason": "authorization_expiry_invalid"}
            if expires_at.astimezone(UTC) <= now:
                return {"ok": False, "reason": "authorization_expired"}

            consent = connection.execute(
                """
                SELECT status, project_id, provider_profile_id, policy_snapshot_artifact_id, accepted_at, revoked_at
                FROM cloud_processing_consents
                WHERE id = ?
                """,
                (cloud_consent_id,),
            ).fetchone()
            if consent is None or consent["status"] != "GRANTED":
                return {"ok": False, "reason": "consent_missing_or_not_granted"}
            if consent["project_id"] != project_id or consent["provider_profile_id"] != provider_profile_id:
                return {"ok": False, "reason": "consent_scope_mismatch"}
            if consent["revoked_at"] is not None or consent["accepted_at"] is None:
                return {"ok": False, "reason": "consent_not_active"}

            profile = connection.execute(
                """
                SELECT adapter_name, model
                FROM provider_profiles
                WHERE id = ? AND enabled = 1
                """,
                (provider_profile_id,),
            ).fetchone()
            if profile is None or "qwen" not in str(profile["adapter_name"]).lower():
                return {"ok": False, "reason": "provider_profile_mismatch"}

            artifact = connection.execute(
                """
                SELECT status, relative_path, sha256
                FROM artifacts
                WHERE id = ?
                """,
                (consent["policy_snapshot_artifact_id"],),
            ).fetchone()
            if artifact is None or artifact["status"] != "READY":
                return {"ok": False, "reason": "policy_snapshot_not_ready"}
            if not _artifact_is_valid(data_root, str(artifact["relative_path"]), str(artifact["sha256"])):
                return {"ok": False, "reason": "policy_snapshot_checksum_invalid"}

            return {"ok": True, "model": profile["model"]}
    except sqlite3.Error:
        return {"ok": False, "reason": "database_unavailable"}
    finally:
        if connection is not None:
            connection.close()


def _artifact_is_valid(data_root: Path, relative_path: str, expected_sha256: str) -> bool:
    candidate = (data_root / relative_path).resolve()
    try:
        if not candidate.is_relative_to(data_root.resolve()) or not candidate.is_file():
            return False
    except OSError:
        return False
    return hashlib.sha256(candidate.read_bytes()).hexdigest() == expected_sha256


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-path", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--provider-profile-id", required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--cloud-consent-id", required=True)
    parser.add_argument("--operation-id", default="poc-qwen")
    parser.add_argument("--api-key", required=True)
    args = parser.parse_args()
    result = run_probe(
        database_path=Path(args.database_path),
        data_root=Path(args.data_root),
        project_id=args.project_id,
        provider_profile_id=args.provider_profile_id,
        authorization_id=args.authorization_id,
        cloud_consent_id=args.cloud_consent_id,
        operation_id=args.operation_id,
        api_key=args.api_key,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {"ok", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
