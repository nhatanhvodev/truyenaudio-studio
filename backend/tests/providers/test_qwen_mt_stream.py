from __future__ import annotations

import json
from typing import Any

import pytest

from app.contracts import OperationContext, TranslationRequest, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked, CloudCallDecision
from app.providers.qwen_mt import ProviderBillingUnknown, QwenMtAdapter, Secret
from app.providers.transport import ProviderTransportError


def _request(**overrides: object) -> TranslationRequest:
    context = OperationContext(
        operation_id="stream-segment-001",
        cache_key="cache-stream",
        timeout_seconds=12,
        estimated_units=42,
        budget_authorization_id="auth-001",
        cloud_consent_id="consent-001",
    )
    payload: dict[str, object] = {
        "context": context,
        "source_segment_id": "seg-1",
        "source_text": "她打开门。",
        "source_language": "zh-CN",
        "target_language": "vi-VN",
        "terms": (),
        "tm_list": (),
        "domain_instruction": "",
        "story_memory": (),
    }
    payload.update(overrides)
    return TranslationRequest(**payload)  # type: ignore[arg-type]


def _sse_frame(payload: dict[str, Any] | str) -> bytes:
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"data: {data}\n\n".encode("utf-8")


def _delta_frame(content: str) -> dict[str, Any]:
    return {"output": {"choices": [{"message": {"role": "assistant", "content": content}}]}}


class _StreamContext:
    def __init__(self, response: "_StreamResponse") -> None:
        self._response = response

    async def __aenter__(self) -> "_StreamResponse":
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _StreamResponse:
    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self._chunks = chunks
        self.status_code = status_code

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _StreamClient:
    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self.chunks = chunks
        self.status_code = status_code
        self.calls: list[dict[str, Any]] = []

    def stream(self, method: str, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers, "timeout": timeout})
        return _StreamContext(_StreamResponse(self.chunks, self.status_code))


def _adapter(client: object) -> QwenMtAdapter:
    return QwenMtAdapter(
        client,
        "qwen-mt-flash",
        "frankfurt",
        Secret("x"),
        cloud_guard=AllowingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
    )


async def _collect(adapter: QwenMtAdapter, request: TranslationRequest) -> list[str]:
    return [delta async for delta in adapter.stream_translate(request)]


@pytest.mark.asyncio
async def test_incremental_stream_yields_deltas_and_records_usage() -> None:
    chunks = [
        _sse_frame(_delta_frame("Xin ")),
        _sse_frame(_delta_frame("chào")),
        _sse_frame(
            {
                "usage": {"input_tokens": 11, "output_tokens": 5},
                "request_id": "req-1",
            }
        ),
        _sse_frame("[DONE]"),
    ]
    client = _StreamClient(chunks)
    adapter = _adapter(client)

    deltas = await _collect(adapter, _request())

    assert deltas == ["Xin ", "chào"]
    assert adapter.last_stream_usage[0].unit == UsageUnit.INPUT_TOKEN.value
    assert adapter.last_stream_usage[0].measured_units == 11
    assert adapter.last_stream_usage[0].provider_request_id == "req-1"
    call = client.calls[0]
    assert call["headers"]["X-DashScope-SSE"] == "enable"
    assert call["json"]["parameters"]["incremental_output"] is True
    assert "system" not in json.dumps(call["json"])


@pytest.mark.asyncio
async def test_non_incremental_full_frames_are_deduplicated() -> None:
    client = _StreamClient([_sse_frame(_delta_frame("Xin")), _sse_frame(_delta_frame("Xin chào"))])
    adapter = _adapter(client)

    deltas = await _collect(adapter, _request())

    assert deltas == ["Xin", " chào"]


@pytest.mark.asyncio
async def test_stream_survives_utf8_split_across_chunks() -> None:
    frame = _sse_frame(_delta_frame("Xin chào bạn"))
    midpoint = len(frame) // 2
    client = _StreamClient([frame[:midpoint], frame[midpoint:]])
    adapter = _adapter(client)

    deltas = await _collect(adapter, _request())

    assert deltas == ["Xin chào bạn"]


@pytest.mark.asyncio
async def test_stream_regression_is_reported_as_malformed() -> None:
    client = _StreamClient(
        [
            _sse_frame(_delta_frame("Xin")),
            _sse_frame(_delta_frame("Xin chào")),
            _sse_frame(_delta_frame("Xin")),
        ]
    )
    adapter = _adapter(client)

    with pytest.raises(ProviderTransportError) as caught:
        await _collect(adapter, _request())

    assert caught.value.code == "MALFORMED_STREAM"


@pytest.mark.asyncio
async def test_non_200_stream_uses_shared_transport_error() -> None:
    client = _StreamClient([], status_code=503)
    adapter = _adapter(client)

    with pytest.raises(ProviderTransportError) as caught:
        await _collect(adapter, _request())

    assert caught.value.code == "HTTP_503"


@pytest.mark.asyncio
async def test_truncated_stream_is_reported() -> None:
    partial = _sse_frame(_delta_frame("Xin"))[:-2]  # missing the blank-line frame terminator
    client = _StreamClient([partial])
    adapter = _adapter(client)

    with pytest.raises(ProviderTransportError) as caught:
        await _collect(adapter, _request())

    assert caught.value.code == "TRUNCATED_STREAM"


@pytest.mark.asyncio
async def test_stream_guard_blocks_before_any_bytes() -> None:
    client = _StreamClient([_sse_frame(_delta_frame("Xin"))])
    adapter = _adapter(client)
    no_consent = _request(
        context=OperationContext(
            operation_id="stream-segment-002",
            cache_key="cache-stream-2",
            timeout_seconds=12,
            estimated_units=42,
            budget_authorization_id="auth-001",
            cloud_consent_id=None,
        )
    )

    with pytest.raises(CloudCallBlocked):
        await _collect(adapter, no_consent)

    assert client.calls == []


class AllowingGuard:
    def evaluate(self, **kwargs: object) -> CloudCallDecision:
        return CloudCallDecision(
            allowed=True,
            cloud_consent_id=str(kwargs["cloud_consent_id"]),
            authorization_id=str(kwargs["budget_authorization_id"]),
            rate_card_ids=("rate-card-001",),
            remaining_quota=(),
            reasons=(),
        )


# ProviderBillingUnknown is part of the adapter contract for ambiguous sends.
assert ProviderBillingUnknown is not None
