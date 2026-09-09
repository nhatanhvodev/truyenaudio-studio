from __future__ import annotations

import asyncio

import pytest

from app.modules.execution.contracts import BillingState
from app.providers.transport import (
    IncrementalSseParser,
    ProviderTransportError,
    bearer_json_headers,
    normalize_http_error,
    normalize_transport_exception,
    redacted_header_snapshot,
)


def test_sse_parser_handles_utf8_and_json_split_across_frames() -> None:
    parser = IncrementalSseParser()
    assert parser.feed(b"event: delta\ndata: {\"text\": \"") == []
    assert parser.feed("xin chào".encode()) == []
    events = parser.feed(b"\"}\n\n")
    assert events == [{"event": "delta", "data": {"text": "xin chào"}}]
    parser.finish()


def test_sse_parser_accepts_crlf_frames_split_across_chunks() -> None:
    parser = IncrementalSseParser()
    assert parser.feed(b"event: delta\r") == []
    assert parser.feed(b"\ndata: {\"ok\": true}\r\n") == []
    assert parser.feed(b"\r\n") == [{"event": "delta", "data": {"ok": True}}]
    parser.finish()


def test_sse_parser_accepts_provider_done_sentinel() -> None:
    parser = IncrementalSseParser()
    assert parser.feed(b"data: [DONE]\n\n") == [{"event": "message", "data": {"done": True}}]
    parser.finish()


def test_sse_parser_rejects_malformed_json_and_truncated_frame() -> None:
    parser = IncrementalSseParser()
    with pytest.raises(ProviderTransportError) as error:
        parser.feed(b"data: nope\n\n")
    assert error.value.code == "MALFORMED_STREAM"
    assert error.value.billing_state is BillingState.UNKNOWN

    parser = IncrementalSseParser()
    parser.feed(b"data: {\"ok\": true}")
    with pytest.raises(ProviderTransportError) as error:
        parser.finish()
    assert error.value.code == "TRUNCATED_STREAM"
    assert error.value.billing_state is BillingState.UNKNOWN


@pytest.mark.parametrize(
    ("status_code", "retryable", "billing_state"),
    [
        (400, False, BillingState.KNOWN),
        (401, False, BillingState.KNOWN),
        (403, False, BillingState.KNOWN),
        (404, False, BillingState.KNOWN),
        (429, True, BillingState.UNKNOWN),
        (500, True, BillingState.UNKNOWN),
        (503, True, BillingState.UNKNOWN),
    ],
)
def test_http_error_normalization_matrix_after_send(
    status_code: int,
    retryable: bool,
    billing_state: BillingState,
) -> None:
    error = normalize_http_error(status_code, "secret=do-not-leak", request_sent=True)

    assert isinstance(error, ProviderTransportError)
    assert error.code == f"HTTP_{status_code}"
    assert error.retryable is retryable
    assert error.billing_state is billing_state
    assert "secret" not in str(error)


def test_http_error_before_send_is_not_sent() -> None:
    error = normalize_http_error(401, "secret=do-not-leak", request_sent=False)

    assert error.billing_state is BillingState.NOT_SENT
    assert error.retryable is False
    assert "secret" not in str(error)


@pytest.mark.parametrize(
    ("exc", "request_started", "code", "billing_state"),
    [
        (TimeoutError("read timed out"), False, "PROVIDER_TIMEOUT", BillingState.NOT_SENT),
        (TimeoutError("read timed out"), True, "PROVIDER_TIMEOUT", BillingState.UNKNOWN),
        (ConnectionError("disconnect"), False, "PROVIDER_NETWORK", BillingState.NOT_SENT),
        (ConnectionError("disconnect"), True, "PROVIDER_NETWORK", BillingState.UNKNOWN),
        (asyncio.CancelledError(), False, "PROVIDER_CANCELLED", BillingState.NOT_SENT),
        (asyncio.CancelledError(), True, "PROVIDER_CANCELLED", BillingState.UNKNOWN),
    ],
)
def test_transport_exception_normalization_preserves_billing_boundary(
    exc: BaseException,
    request_started: bool,
    code: str,
    billing_state: BillingState,
) -> None:
    error = normalize_transport_exception(exc, request_started=request_started)

    assert error.code == code
    assert error.billing_state is billing_state


def test_bearer_headers_and_safe_snapshot_do_not_log_key() -> None:
    headers = bearer_json_headers("secret-token", extra={"X-Request-ID": "request-1"})

    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Request-ID"] == "request-1"
    assert redacted_header_snapshot(headers) == {
        "Authorization": "[REDACTED]",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Request-ID": "request-1",
    }


def test_bearer_headers_reject_blank_key_before_request() -> None:
    with pytest.raises(ProviderTransportError) as error:
        bearer_json_headers(" ")

    assert error.value.code == "PROVIDER_AUTH_MISSING"
    assert error.value.billing_state is BillingState.NOT_SENT
