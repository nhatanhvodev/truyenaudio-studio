from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import re
from typing import Any
from urllib.parse import unquote

from app.modules.security.secret_keys import is_secret_key as _is_secret_key


JSONL_FIELDS = (
    "timestamp",
    "level",
    "correlationId",
    "jobId",
    "attempt",
    "stage",
    "provider",
    "model",
    "latencyMs",
    "cacheHit",
    "units",
    "costVnd",
    "errorCode",
)

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:bearer|token|api[_-]?key|secret)\s+['\"]?[A-Za-z0-9._\-:/+=]{4,}", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"([?&](?:api[_-]?key|token|secret|access[_-]?token)=[^&#\s]+)", re.IGNORECASE),
)
QUERY_PARAMETER_PATTERN = re.compile(r"([?&])([^=&#\s]+)=([^&#\s]+)")


class DiagnosticsLogger:
    def __init__(
        self,
        log_dir: Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        stream_logger: logging.Logger | None = None,
    ) -> None:
        self.log_dir = log_dir
        self.clock = clock
        self.stream_logger = stream_logger or logging.getLogger("app.diagnostics")

    def job_error(
        self,
        *,
        correlation_id: str,
        job_id: str,
        attempt: int,
        stage: str,
        provider: str | None,
        model: str | None,
        latency_ms: int | None,
        cache_hit: bool | None,
        units: int | None,
        cost_vnd: int | None,
        error_code: str,
        error: Exception,
        **_blocked_context: object,
    ) -> dict[str, object]:
        record = self._record(
            "ERROR",
            correlation_id=correlation_id,
            job_id=job_id,
            attempt=attempt,
            stage=stage,
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            cache_hit=cache_hit,
            units=units,
            cost_vnd=cost_vnd,
            error_code=error_code,
        )
        self.write(record)
        safe_record = _redacted_payload(record)
        self.stream_logger.error(json.dumps(safe_record, ensure_ascii=False, separators=(",", ":")))
        return safe_record

    def info(self, **fields: object) -> dict[str, object]:
        record = self._record("INFO", **fields)
        self.write(record)
        safe_record = _redacted_payload(record)
        self.stream_logger.info(json.dumps(safe_record, ensure_ascii=False, separators=(",", ":")))
        return safe_record

    def write(self, record: dict[str, object]) -> None:
        now = self._now_utc()
        path = self.log_dir / f"diagnostics-{now.date().isoformat()}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = _redacted_payload(record)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")

    def _record(self, level: str, **fields: object) -> dict[str, object]:
        now = self._now_utc()
        record: dict[str, object] = {field: None for field in JSONL_FIELDS}
        record["timestamp"] = now.isoformat()
        record["level"] = level
        aliases = {
            "correlation_id": "correlationId",
            "job_id": "jobId",
            "latency_ms": "latencyMs",
            "cache_hit": "cacheHit",
            "cost_vnd": "costVnd",
            "error_code": "errorCode",
            "error_summary": "errorSummary",
        }
        for key, value in fields.items():
            field = aliases.get(key, key)
            if field in record:
                record[field] = value
        return record

    def _now_utc(self) -> datetime:
        return self.clock().astimezone(UTC)


def summarize_error(error: Exception) -> str:
    summary = scrub_text(str(error))
    if len(summary) > 500:
        return summary[:497] + "..."
    return summary


def scrub_text(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _is_secret_key(key) else scrub_text(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [scrub_text(item) for item in value]
    if not isinstance(value, str):
        return value
    scrubbed = value
    for pattern in SECRET_PATTERNS:
        scrubbed = pattern.sub("[REDACTED]", scrubbed)
    return _scrub_query_parameters(scrubbed)


def _redacted_payload(record: dict[str, object]) -> dict[str, object]:
    return {field: scrub_text(record.get(field)) for field in JSONL_FIELDS if field in record}


def _scrub_query_parameters(value: str) -> str:
    def redact_secret_param(match: re.Match[str]) -> str:
        separator, key, _secret = match.groups()
        if _is_secret_query_key(key):
            return f"{separator}{key}=[REDACTED]"
        return match.group(0)

    return QUERY_PARAMETER_PATTERN.sub(redact_secret_param, value)


def _is_secret_query_key(key: str) -> bool:
    candidate = key
    for _round in range(4):
        if _is_secret_key(candidate):
            return True
        decoded = unquote(candidate)
        if decoded == candidate:
            return False
        candidate = decoded
    return "%" in candidate or _is_secret_key(candidate)
