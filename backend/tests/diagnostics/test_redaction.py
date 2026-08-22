from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.modules.diagnostics.logging import DiagnosticsLogger


def test_job_error_writes_allowlisted_jsonl_without_secret_or_full_text(
    tmp_path: Path,
    caplog,
) -> None:
    logger = DiagnosticsLogger(
        tmp_path,
        clock=lambda: datetime(2026, 8, 19, 1, 2, 3, tzinfo=UTC),
        stream_logger=logging.getLogger("tests.diagnostics.redaction"),
    )
    source_text = "秘密全文" * 80
    translation_text = "ban dich day du " * 80

    with caplog.at_level(logging.ERROR, logger="tests.diagnostics.redaction"):
        logger.job_error(
            correlation_id="corr-secret",
            job_id="job-1",
            attempt=2,
            stage="TRANSLATE",
            provider="qwen",
            model="qwen-local",
            latency_ms=123,
            cache_hit=False,
            units=456,
            cost_vnd=789,
            error_code="PROVIDER_ERROR",
            error=RuntimeError("failed with sk-test-1234567890abcdef and bearer secret"),
            secret="sk-test-1234567890abcdef",
            source_text=source_text,
            translation_text=translation_text,
        )

    rendered = caplog.text
    payload = (tmp_path / "diagnostics-2026-08-19.jsonl").read_text(encoding="utf-8")
    record = json.loads(payload)

    assert set(record) == {
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
        "errorSummary",
    }
    assert record["timestamp"] == "2026-08-19T01:02:03+00:00"
    assert record["level"] == "ERROR"
    assert record["errorSummary"] == "failed with [REDACTED] and [REDACTED]"
    assert len(record["errorSummary"]) <= 500
    assert "sk-test-1234567890abcdef" not in payload
    assert "sk-test-1234567890abcdef" not in rendered
    assert "秘密全文" not in payload
    assert "秘密全文" not in rendered
    assert "ban dich day du" not in payload
    assert "ban dich day du" not in rendered
