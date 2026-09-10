"""Offline contract tests for the Cerebras adapter (plan X03).

Every case is hermetic: a stub HTTP client stands in for the network, and no
socket is opened. The stub records exactly what the adapter would put on the
wire, so the native chat-completions shape, the credential placement and the
error/billing-state mapping are asserted against real adapter behaviour.

NOT RUN here (and not claimed): no live Cerebras account, key, region or terms
were exercised; the trial/quota figures in the research note stay unverified.
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from typing import Any

import httpx
import pytest

from app.contracts import OperationContext, TranslationRequest, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked, CloudCallDecision
from app.modules.execution.contracts import BillingState
from app.providers.cerebras import (
    ENDPOINT,
    PROVIDER,
    CerebrasAdapter,
    ProviderBillingUnknown,
)
from app.providers.transport import ProviderTransportError
from tests.providers.contract_suite import install_socket_tripwire


SECRET = "cerebras-key-do-not-leak-001"
REQUESTED_MODEL = "gpt-oss-120b"
REPORTED_MODEL = "qwen-3-32b"
TRANSLATED = "Cô ấy mở cửa."


def _request(**overrides: object) -> TranslationRequest:
    payload: dict[str, object] = {
        "context": OperationContext(
            operation_id="op-cerebras-1",
            cache_key="cache-cerebras-1",
            timeout_seconds=17,
            estimated_units=42,
            budget_authorization_id="auth-1",
            cloud_consent_id="consent-1",
        ),
        "source_segment_id": "seg-1",
        "source_text": "她打开门。",
        "source_language": "zh-CN",
        "target_language": "vi-VN",
        "terms": (("门", "cửa"),),
        "tm_list": (),
        "domain_instruction": "natural",
        "story_memory": (),
    }
    payload.update(overrides)
    return TranslationRequest(**payload)  # type: ignore[arg-type]


@pytest.fixture
def translation_request() -> TranslationRequest:
    return _request()


def _chat_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "chatcmpl-cerebras-1",
        "model": REPORTED_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": TRANSLATED},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 13, "completion_tokens": 9, "total_tokens": 22},
        "time_info": {"queue_time": 0.001},
    }
    body.update(overrides)
    return body


class Response:
    def __init__(self, body: Any, status_code: int = 200) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class Http:
    """Stub chat-completions client that records the outgoing request."""

    def __init__(self, response: Response | None = None) -> None:
        self.response = response if response is not None else Response(_chat_body())
        self.calls = 0
        self.last_url = ""
        self.last_json: dict[str, Any] = {}
        self.last_headers: dict[str, str] = {}
        self.last_timeout: int | None = None

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int) -> Response:
        self.calls += 1
        self.last_url = url
        self.last_json = json
        self.last_headers = headers
        self.last_timeout = timeout
        return self.response


class StatusHttp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls = 0

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int) -> Response:
        self.calls += 1
        return Response({"error": {"message": f"upstream echoed secret={SECRET}"}}, self.status_code)


class RaisingHttp:
    def __init__(self, exception: Exception) -> None:
        self.exception = exception
        self.calls = 0
        self.last_json: dict[str, Any] = {}

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int) -> Response:
        self.calls += 1
        self.last_json = json
        raise self.exception


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


class DenyingGuard:
    def evaluate(self, **kwargs: object) -> CloudCallDecision:
        return CloudCallDecision(
            allowed=False,
            cloud_consent_id=None,
            authorization_id=None,
            rate_card_ids=(),
            remaining_quota=(),
            reasons=("CONSENT_NOT_GRANTED",),
        )


def _adapter(http: object, model: str = REQUESTED_MODEL) -> CerebrasAdapter:
    return CerebrasAdapter(
        http,
        model,
        SECRET,
        cloud_guard=AllowingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
    )


def _string_leaves(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if is_dataclass(value) and not isinstance(value, type):
        leaves: list[str] = []
        for field in fields(value):
            leaves.extend(_string_leaves(getattr(value, field.name)))
        return leaves
    if isinstance(value, (tuple, list)):
        leaves = []
        for item in value:
            leaves.extend(_string_leaves(item))
        return leaves
    return []


# --- translate: native wire shape -------------------------------------------


@pytest.mark.asyncio
async def test_cerebras_translate_maps_native_chat_completions_payload(translation_request: TranslationRequest) -> None:
    http = Http()

    result = await _adapter(http).translate(translation_request)

    assert result.target_text == TRANSLATED
    assert result.provider == PROVIDER
    assert result.model == REPORTED_MODEL
    assert result.provider_version == "chat.completions"
    assert http.calls == 1
    assert http.last_url == ENDPOINT
    assert http.last_timeout == translation_request.context.timeout_seconds
    assert http.last_headers["Authorization"] == f"Bearer {SECRET}"
    assert http.last_headers["Content-Type"] == "application/json"
    assert http.last_json["model"] == REQUESTED_MODEL
    assert http.last_json["stream"] is False
    assert [message["role"] for message in http.last_json["messages"]] == ["system", "user"]
    user_content = http.last_json["messages"][-1]["content"]
    assert "她打开门" in user_content
    assert "门 -> cửa" in user_content
    assert "zh-CN" in user_content
    assert "vi-VN" in user_content
    assert set(http.last_json) == {"model", "messages", "stream"}


@pytest.mark.asyncio
async def test_cerebras_maps_usage_tokens_and_request_id(translation_request: TranslationRequest) -> None:
    http = Http()

    result = await _adapter(http).translate(translation_request)

    assert result.usage[0].unit == UsageUnit.INPUT_TOKEN.value
    assert result.usage[0].measured_units == 13
    assert result.usage[0].provider_request_id == "chatcmpl-cerebras-1"
    assert result.usage[1].unit == UsageUnit.OUTPUT_TOKEN.value
    assert result.usage[1].measured_units == 9
    assert result.usage[1].provider_request_id == "chatcmpl-cerebras-1"


@pytest.mark.asyncio
async def test_cerebras_reports_provider_actual_model_instead_of_requested(translation_request: TranslationRequest) -> None:
    http = Http()

    result = await _adapter(http, model="gpt-oss-120b").translate(translation_request)

    assert result.model == REPORTED_MODEL
    assert result.model != "gpt-oss-120b"
    # The requested model is what was sent; the substitution is only surfaced,
    # never hidden and never silently applied on the wire.
    assert http.last_json["model"] == "gpt-oss-120b"


@pytest.mark.asyncio
async def test_cerebras_falls_back_to_requested_model_only_when_provider_omits_it(
    translation_request: TranslationRequest,
) -> None:
    http = Http(Response(_chat_body(model=None)))

    result = await _adapter(http).translate(translation_request)

    assert result.model == REQUESTED_MODEL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage",
    [
        None,
        "13",
        {"prompt_tokens": "13", "completion_tokens": 9.5},
        {"prompt_tokens": -4, "completion_tokens": True},
    ],
)
async def test_cerebras_usage_defaults_to_zero_without_provider_numbers(
    translation_request: TranslationRequest,
    usage: object,
) -> None:
    http = Http(Response(_chat_body(usage=usage)))

    result = await _adapter(http).translate(translation_request)

    assert result.usage[0].measured_units == 0
    assert result.usage[1].measured_units == 0


# --- guard -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cerebras_guard_missing_blocks_before_any_http(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = CerebrasAdapter(http, REQUESTED_MODEL, SECRET)

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CLOUD_GUARD_REQUIRED",)
    assert http.calls == 0


@pytest.mark.asyncio
async def test_cerebras_guard_precedes_source_validation(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = CerebrasAdapter(http, REQUESTED_MODEL, SECRET)

    with pytest.raises(CloudCallBlocked):
        await adapter.translate(_request(source_text="   "))

    assert http.calls == 0


@pytest.mark.asyncio
async def test_cerebras_guard_context_missing_blocks_before_any_http(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = CerebrasAdapter(http, REQUESTED_MODEL, SECRET, cloud_guard=AllowingGuard())

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CLOUD_GUARD_CONTEXT_REQUIRED",)
    assert http.calls == 0


@pytest.mark.asyncio
async def test_cerebras_denied_guard_blocks_before_any_http(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = CerebrasAdapter(
        http,
        REQUESTED_MODEL,
        SECRET,
        cloud_guard=DenyingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CONSENT_NOT_GRANTED",)
    assert http.calls == 0


@pytest.mark.asyncio
async def test_cerebras_rejects_empty_source_before_any_http(translation_request: TranslationRequest) -> None:
    http = Http()

    with pytest.raises(ValueError, match="CEREBRAS_SOURCE_REQUIRED"):
        await _adapter(http).translate(_request(source_text="   "))

    assert http.calls == 0


# --- error normalisation -----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "billing_state", "retryable"),
    [
        (400, BillingState.KNOWN, False),
        (401, BillingState.KNOWN, False),
        (403, BillingState.KNOWN, False),
        (404, BillingState.KNOWN, False),
        (429, BillingState.UNKNOWN, True),
        (500, BillingState.UNKNOWN, True),
        (503, BillingState.UNKNOWN, True),
    ],
)
async def test_cerebras_http_error_matrix_maps_billing_state(
    translation_request: TranslationRequest,
    status_code: int,
    billing_state: BillingState,
    retryable: bool,
) -> None:
    http = StatusHttp(status_code)

    with pytest.raises(ProviderTransportError) as exc:
        await _adapter(http).translate(translation_request)

    assert exc.value.code == f"HTTP_{status_code}"
    assert exc.value.billing_state is billing_state
    assert exc.value.retryable is retryable
    assert http.calls == 1
    assert SECRET not in str(exc.value)


@pytest.mark.asyncio
async def test_cerebras_timeout_after_send_is_billing_unknown_without_retry(
    translation_request: TranslationRequest,
) -> None:
    http = RaisingHttp(TimeoutError("provider never answered"))

    with pytest.raises(ProviderBillingUnknown) as exc:
        await _adapter(http).translate(translation_request)

    assert str(exc.value) == "CEREBRAS_BILLING_UNKNOWN"
    # Exactly one dispatch: the adapter never retries an ambiguous send.
    assert http.calls == 1
    assert "她打开门" in http.last_json["messages"][-1]["content"]
    assert SECRET not in repr(http.last_json)
    assert SECRET not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception",
    [httpx.ReadTimeout("read timed out"), httpx.ConnectError("connect unclear"), httpx.RemoteProtocolError("lost")],
)
async def test_cerebras_transport_error_after_send_is_billing_unknown(
    translation_request: TranslationRequest,
    exception: Exception,
) -> None:
    http = RaisingHttp(exception)

    with pytest.raises(ProviderBillingUnknown):
        await _adapter(http).translate(translation_request)

    assert http.calls == 1


@pytest.mark.asyncio
async def test_cerebras_json_decode_failure_fails_closed_as_unknown(translation_request: TranslationRequest) -> None:
    http = Http(Response(ValueError(f"secret={SECRET}")))

    with pytest.raises(ProviderTransportError) as exc:
        await _adapter(http).translate(translation_request)

    assert exc.value.code == "MALFORMED_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN
    assert SECRET not in str(exc.value)


@pytest.mark.asyncio
async def test_cerebras_non_object_body_is_malformed(translation_request: TranslationRequest) -> None:
    http = Http(Response([{"model": REPORTED_MODEL}]))

    with pytest.raises(ProviderTransportError) as exc:
        await _adapter(http).translate(translation_request)

    assert exc.value.code == "MALFORMED_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "choices",
    [[], "not-a-list", [{}], [{"message": "not-a-dict"}], [{"message": {"content": "   "}}]],
)
async def test_cerebras_empty_choices_are_empty_response(
    translation_request: TranslationRequest,
    choices: object,
) -> None:
    http = Http(Response(_chat_body(choices=choices, usage=None)))

    with pytest.raises(ProviderTransportError) as exc:
        await _adapter(http).translate(translation_request)

    assert exc.value.code == "EMPTY_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN


@pytest.mark.asyncio
async def test_cerebras_never_places_or_returns_the_api_key(translation_request: TranslationRequest) -> None:
    http = Http()

    result = await _adapter(http).translate(translation_request)

    assert SECRET not in json.dumps(http.last_json, ensure_ascii=False)
    assert SECRET not in http.last_url
    assert "key" not in json.dumps(http.last_json)
    assert http.last_headers == {
        "Authorization": f"Bearer {SECRET}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    assert all(SECRET not in leaf for leaf in _string_leaves(result))
    assert SECRET not in repr(result)

    failing = StatusHttp(401)
    with pytest.raises(ProviderTransportError) as exc:
        await _adapter(failing).translate(translation_request)
    assert SECRET not in str(exc.value)
    assert SECRET not in repr(exc.value)


@pytest.mark.asyncio
async def test_cerebras_contract_runs_without_opening_a_socket(
    translation_request: TranslationRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls = install_socket_tripwire(monkeypatch)
    http = Http()

    result = await _adapter(http).translate(translation_request)

    assert result.provider == PROVIDER
    assert http.calls == 1
    assert network_calls == []


def test_cerebras_capabilities_describe_the_wire_surface() -> None:
    adapter = _adapter(Http())

    capabilities = adapter.capabilities()

    assert capabilities["provider"] == PROVIDER
    assert capabilities["model"] == REQUESTED_MODEL
    assert capabilities["api_kind"] == "chat"
    assert capabilities["network"] is True
    assert SECRET not in json.dumps(capabilities)
    assert adapter.capabilities() == capabilities


# --- SSE stream: split / finish ---------------------------------------------


def _sse_frame(payload: dict[str, Any] | str) -> bytes:
    data = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"data: {data}\n\n".encode("utf-8")


def _delta_frame(content: str, *, model: str = REPORTED_MODEL, chunk_id: str = "chunk-1") -> dict[str, Any]:
    return {
        "id": chunk_id,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}],
    }


class StreamContext:
    def __init__(self, response: StreamResponse) -> None:
        self._response = response

    async def __aenter__(self) -> StreamResponse:
        return self._response

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class StreamResponse:
    def __init__(self, chunks: list[bytes], status_code: int = 200, error: Exception | None = None) -> None:
        self._chunks = chunks
        self._error = error
        self.status_code = status_code

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk
        if self._error is not None:
            raise self._error


class StreamClient:
    def __init__(self, chunks: list[bytes], status_code: int = 200, error: Exception | None = None) -> None:
        self.chunks = chunks
        self.status_code = status_code
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def stream(self, method: str, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers, "timeout": timeout})
        return StreamContext(StreamResponse(self.chunks, self.status_code, self.error))


async def _collect(adapter: CerebrasAdapter, request: TranslationRequest) -> list[str]:
    return [delta async for delta in adapter.stream_translate(request)]


@pytest.mark.asyncio
async def test_cerebras_stream_survives_split_utf8_and_split_json(translation_request: TranslationRequest) -> None:
    frame = _sse_frame(_delta_frame(TRANSLATED))
    marker = "Cô".encode("utf-8")  # b"C\xc3\xb4"
    boundary = frame.index(marker) + 2  # lands inside the 2-byte sequence for "ô"
    client = StreamClient([frame[:boundary], frame[boundary : boundary + 3], frame[boundary + 3 :]])
    adapter = _adapter(client)

    deltas = await _collect(adapter, translation_request)

    assert deltas == [TRANSLATED]
    assert "".join(deltas) == TRANSLATED


@pytest.mark.asyncio
async def test_cerebras_stream_survives_byte_by_byte_chunks(translation_request: TranslationRequest) -> None:
    frame = _sse_frame(_delta_frame("Xin chào bạn"))
    client = StreamClient([frame[index : index + 1] for index in range(len(frame))])
    adapter = _adapter(client)

    deltas = await _collect(adapter, translation_request)

    assert deltas == ["Xin chào bạn"]


@pytest.mark.asyncio
async def test_cerebras_stream_finish_marks_done_and_records_actual_model_and_usage(
    translation_request: TranslationRequest,
) -> None:
    model = "llama3.1-8b"
    client = StreamClient(
        [
            _sse_frame(_delta_frame("Xin ", model=model, chunk_id="c-1")),
            _sse_frame(_delta_frame("chào", model=model, chunk_id="c-2")),
            _sse_frame(
                {
                    "id": "c-3",
                    "model": model,
                    "choices": [],
                    "usage": {"prompt_tokens": 13, "completion_tokens": 9},
                }
            ),
            _sse_frame("[DONE]"),
        ]
    )
    adapter = _adapter(client, model="gpt-oss-120b")

    deltas = await _collect(adapter, translation_request)
    result = adapter.stream_result("".join(deltas))

    assert deltas == ["Xin ", "chào"]
    assert result.target_text == "Xin chào"
    assert result.model == model
    assert result.model != "gpt-oss-120b"
    assert result.provider_version == "chat.completions"
    assert result.usage[0].measured_units == 13
    assert result.usage[0].provider_request_id == "c-3"
    assert result.usage[1].measured_units == 9
    call = client.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == ENDPOINT
    assert call["json"]["stream"] is True
    assert call["headers"]["Accept"] == "text/event-stream"
    assert call["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert SECRET not in json.dumps(call["json"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_cerebras_stream_without_usage_reports_no_invented_tokens(translation_request: TranslationRequest) -> None:
    client = StreamClient([_sse_frame(_delta_frame("Xin")), _sse_frame("[DONE]")])
    adapter = _adapter(client)

    deltas = await _collect(adapter, translation_request)
    result = adapter.stream_result("".join(deltas))

    assert result.usage == ()
    assert adapter.last_stream_usage == ()


@pytest.mark.asyncio
async def test_cerebras_stream_result_rejects_empty_text(translation_request: TranslationRequest) -> None:
    adapter = _adapter(StreamClient([]))

    with pytest.raises(ProviderTransportError) as exc:
        adapter.stream_result("   ")

    assert exc.value.code == "EMPTY_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN


@pytest.mark.asyncio
async def test_cerebras_truncated_stream_frame_is_reported(translation_request: TranslationRequest) -> None:
    partial = _sse_frame(_delta_frame("Xin"))[:-2]  # missing the blank-line frame terminator
    adapter = _adapter(StreamClient([partial]))

    with pytest.raises(ProviderTransportError) as exc:
        await _collect(adapter, translation_request)

    assert exc.value.code == "TRUNCATED_STREAM"


@pytest.mark.asyncio
async def test_cerebras_stream_cut_inside_the_json_body_is_reported(translation_request: TranslationRequest) -> None:
    frame = _sse_frame(_delta_frame("Cô"))
    adapter = _adapter(StreamClient([frame[:-12]]))  # frame never reaches its blank-line terminator

    with pytest.raises(ProviderTransportError) as exc:
        await _collect(adapter, translation_request)

    assert exc.value.code in {"TRUNCATED_STREAM", "MALFORMED_STREAM"}


@pytest.mark.asyncio
async def test_cerebras_stream_non_200_uses_shared_transport_error(translation_request: TranslationRequest) -> None:
    adapter = _adapter(StreamClient([], status_code=503))

    with pytest.raises(ProviderTransportError) as exc:
        await _collect(adapter, translation_request)

    assert exc.value.code == "HTTP_503"
    assert exc.value.billing_state is BillingState.UNKNOWN


@pytest.mark.asyncio
async def test_cerebras_stream_transport_failure_after_send_is_billing_unknown(
    translation_request: TranslationRequest,
) -> None:
    adapter = _adapter(StreamClient([_sse_frame(_delta_frame("Xin"))], error=httpx.ReadTimeout("read timed out")))

    with pytest.raises(ProviderBillingUnknown) as exc:
        await _collect(adapter, translation_request)

    assert str(exc.value) == "CEREBRAS_BILLING_UNKNOWN"


@pytest.mark.asyncio
async def test_cerebras_stream_guard_blocks_before_any_bytes(translation_request: TranslationRequest) -> None:
    client = StreamClient([_sse_frame(_delta_frame("Xin"))])
    adapter = CerebrasAdapter(client, REQUESTED_MODEL, SECRET)

    with pytest.raises(CloudCallBlocked) as exc:
        await _collect(adapter, translation_request)

    assert exc.value.reasons == ("CLOUD_GUARD_REQUIRED",)
    assert client.calls == []


@pytest.mark.asyncio
async def test_cerebras_stream_rejects_empty_source_before_any_bytes(translation_request: TranslationRequest) -> None:
    client = StreamClient([_sse_frame(_delta_frame("Xin"))])
    adapter = _adapter(client)

    with pytest.raises(ValueError, match="CEREBRAS_SOURCE_REQUIRED"):
        await _collect(adapter, _request(source_text="   "))

    assert client.calls == []
