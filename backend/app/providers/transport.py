"""Shared HTTP error and incremental SSE transport primitives."""

from __future__ import annotations

import codecs
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
    billing_state = BillingState.UNKNOWN if request_sent else BillingState.NOT_SENT
    # Provider bodies can echo credentials or source text; expose a stable generic detail.
    message = "provider request failed"
    if status_code in {401, 403}:
        message = "provider credential rejected"
    elif status_code == 429:
        message = "provider rate limit exceeded"
    return ProviderTransportError(f"HTTP_{status_code}", message, retryable, billing_state, status_code)


class IncrementalSseParser:
    """Parse SSE frames without assuming network chunk or UTF-8 boundaries."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""

    def feed(self, chunk: bytes | str) -> list[dict[str, Any]]:
        try:
            self._buffer += self._decoder.decode(chunk if isinstance(chunk, bytes) else chunk.encode(), final=False)
        except UnicodeDecodeError as exc:
            raise ProviderTransportError("MALFORMED_STREAM", "invalid UTF-8 stream", False, BillingState.UNKNOWN) from exc
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
            try:
                payload = json.loads("\n".join(data_lines))
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

