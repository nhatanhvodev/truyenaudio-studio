from __future__ import annotations

import pytest

from app.modules.execution.contracts import BillingState
from app.providers.transport import IncrementalSseParser, ProviderTransportError, normalize_http_error


def test_sse_parser_handles_utf8_and_json_split_across_frames() -> None:
    parser = IncrementalSseParser()
    assert parser.feed(b"event: delta\ndata: {\"text\": \"") == []
    assert parser.feed("xin chào".encode()) == []
    events = parser.feed(b"\"}\n\n")
    assert events == [{"event": "delta", "data": {"text": "xin chào"}}]
    parser.finish()


def test_sse_parser_accepts_crlf_frames() -> None:
    parser = IncrementalSseParser()
    assert parser.feed(b"event: delta\r\ndata: {\"ok\": true}\r\n\r\n") == [
        {"event": "delta", "data": {"ok": True}}
    ]
    parser.finish()


def test_sse_parser_rejects_malformed_json_and_truncated_frame() -> None:
    parser = IncrementalSseParser()
    with pytest.raises(ProviderTransportError) as error:
        parser.feed(b"data: nope\n\n")
    assert error.value.code == "MALFORMED_STREAM"
    parser = IncrementalSseParser()
    parser.feed(b"data: {\"ok\": true}")
    with pytest.raises(ProviderTransportError) as error:
        parser.finish()
    assert error.value.code == "TRUNCATED_STREAM"


def test_http_error_normalization_preserves_billing_unknown_after_send() -> None:
    error = normalize_http_error(429, "rate limited", request_sent=True)
    assert isinstance(error, ProviderTransportError)
    assert error.code == "HTTP_429"
    assert error.retryable is True
    assert error.billing_state is BillingState.UNKNOWN

    unsent = normalize_http_error(401, "secret=do-not-leak", request_sent=False)
    assert unsent.billing_state is BillingState.NOT_SENT
    assert "secret" not in str(unsent)
