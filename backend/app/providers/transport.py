"""Shared HTTP error and incremental SSE transport primitives."""

from __future__ import annotations

import asyncio
import codecs
from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import Any

from app.modules.execution.contracts import BillingState


@dataclass
class ProviderTransportError(Exception):
    code: str
    message: str
    retryable: bool
    billing_state: BillingState
    status_code: int | None = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)


def normalize_http_error(status_code: int, detail: str, *, request_sent: bool) -> ProviderTransportError:
    retryable = status_code == 408 or status_code == 429 or status_code >= 500
    billing_state = _billing_state_for_http_status(status_code, request_sent=request_sent)
    # Provider bodies can echo credentials or source text; expose a stable generic detail.
    message = "provider request failed"
    if status_code in {401, 403}:
        message = "provider credential rejected"
    elif status_code == 429:
        message = "provider rate limit exceeded"
    return ProviderTransportError(f"HTTP_{status_code}", message, retryable, billing_state, status_code)


def normalize_transport_exception(exc: BaseException, *, request_started: bool) -> ProviderTransportError:
    if isinstance(exc, asyncio.CancelledError):
        return ProviderTransportError(
            "PROVIDER_CANCELLED",
            "provider request was cancelled",
            False,
            BillingState.UNKNOWN if request_started else BillingState.NOT_SENT,
        )
    if isinstance(exc, TimeoutError):
        return ProviderTransportError(
            "PROVIDER_TIMEOUT",
            "provider request timed out",
            True,
            BillingState.UNKNOWN if request_started else BillingState.NOT_SENT,
        )
    if isinstance(exc, (ConnectionError, OSError)):
        return ProviderTransportError(
            "PROVIDER_NETWORK",
            "provider network error",
            True,
            BillingState.UNKNOWN if request_started else BillingState.NOT_SENT,
        )
    return ProviderTransportError(
        "PROVIDER_TRANSPORT",
        "provider transport error",
        False,
        BillingState.UNKNOWN if request_started else BillingState.NOT_SENT,
    )


def bearer_json_headers(token: str, *, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    if not token.strip():
        raise ProviderTransportError("PROVIDER_AUTH_MISSING", "provider credential missing", False, BillingState.NOT_SENT)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    headers.update(extra or {})
    return headers


def redacted_header_snapshot(headers: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in headers.items():
        result[key] = "[REDACTED]" if _is_secret_header(key) else value
    return result


class IncrementalSseParser:
    """Parse SSE frames without assuming network chunk or UTF-8 boundaries."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""

    def feed(self, chunk: bytes | str) -> list[dict[str, Any]]:
        try:
            decoded = self._decoder.decode(chunk if isinstance(chunk, bytes) else chunk.encode(), final=False)
            self._buffer += decoded
        except UnicodeDecodeError as exc:
            raise ProviderTransportError("MALFORMED_STREAM", "invalid UTF-8 stream", False, BillingState.UNKNOWN) from exc
        # SSE permits CRLF; normalize only complete pairs so split boundaries remain safe.
        self._buffer = self._buffer.replace("\r\n", "\n")
        events: list[dict[str, Any]] = []
        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            frame = frame.rstrip("\r")
            if not frame.strip() or frame.startswith(":"):
                continue
            event_name = "message"
            data_lines: list[str] = []
            for line in frame.splitlines():
                if line.startswith("event:"):
                    event_name = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    data_lines.append(line.removeprefix("data:").lstrip())
            if not data_lines:
                raise ProviderTransportError("MALFORMED_STREAM", "SSE frame has no data", False, BillingState.UNKNOWN)
            data_text = "\n".join(data_lines)
            if data_text.strip() == "[DONE]":
                events.append({"event": event_name, "data": {"done": True}})
                continue
            try:
                payload = json.loads(data_text)
            except json.JSONDecodeError as exc:
                raise ProviderTransportError("MALFORMED_STREAM", "SSE data is not JSON", False, BillingState.UNKNOWN) from exc
            events.append({"event": event_name, "data": payload})
        return events

    def finish(self) -> None:
        try:
            self._decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise ProviderTransportError("TRUNCATED_STREAM", "stream ended inside UTF-8 codepoint", False, BillingState.UNKNOWN) from exc
        if self._buffer.strip():
            raise ProviderTransportError("TRUNCATED_STREAM", "stream ended before an SSE frame boundary", False, BillingState.UNKNOWN)


def _billing_state_for_http_status(status_code: int, *, request_sent: bool) -> BillingState:
    if not request_sent:
        return BillingState.NOT_SENT
    if status_code in {400, 401, 403, 404}:
        return BillingState.KNOWN
    return BillingState.UNKNOWN


def _is_secret_header(key: str) -> bool:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    return normalized in {
        "authorization",
        "apikey",
        "xapikey",
        "xgoogapikey",
        "openaikey",
        "xalibabacloudapikey",
    }
