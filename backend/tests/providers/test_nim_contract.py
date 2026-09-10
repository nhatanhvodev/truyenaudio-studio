"""Offline contract tests for the NVIDIA NIM adapter (plan X02).

Every case is hermetic: a stub HTTP client stands in for the network, no socket
is opened (one case proves it with the shared socket tripwire), and nothing here
contacts a NIM deployment.

Fixture pinning: the model and API kind are pinned in this file
(`REQUESTED_MODEL`, `REPORTED_MODEL`, `API_KIND = "chat"`) and asserted on both
the outgoing payload and the result, so a NIM chat surface that silently changed
shape could not pass. The two endpoints used are deliberately *different hosts*
(hosted catalog default vs. a self-hosted base URL) because NIM endpoints are
not equivalent to each other: the adapter must use exactly the endpoint it was
configured with, over https, and refuse anything else.

NOT RUN here (and not claimed): no live NIM account, API key, self-hosted
container, region or licence/terms check was exercised; the per-model quota and
catalog facts in docs/research/provider-research.md [N1]-[N3] stay unverified.
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
from app.providers.nim import (
    BILLING_UNKNOWN,
    ENDPOINT,
    ENDPOINT_UNSUPPORTED,
    MODEL_SOURCE_PROVIDER,
    MODEL_SOURCE_REQUESTED,
    PROVIDER,
    SOURCE_REQUIRED,
    NvidiaNimAdapter,
    ProviderBillingUnknown,
    canonical_nim_endpoint,
)
from app.providers.transport import ProviderTransportError
from tests.providers.contract_suite import install_socket_tripwire


# --- pinned fixture ----------------------------------------------------------
SECRET = "nim-key-do-not-leak-001"
REQUESTED_MODEL = "meta/llama-3.1-70b-instruct"
REPORTED_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1"
API_KIND = "chat"
SELF_HOSTED_ENDPOINT = "https://nim.internal.example:8443/v1/chat/completions"
TRANSLATED = "Cô ấy mở cửa."


def _request(**overrides: object) -> TranslationRequest:
    payload: dict[str, object] = {
        "context": OperationContext(
            operation_id="op-nim-1",
            cache_key="cache-nim-1",
            timeout_seconds=19,
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
        "id": "chatcmpl-nim-1",
        "object": "chat.completion",
        "model": REPORTED_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": TRANSLATED},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 15, "completion_tokens": 11, "total_tokens": 26},
    }
    body.update(overrides)
    return body


def _chat_body_without_choices() -> dict[str, Any]:
    body = _chat_body()
    del body["choices"]
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
        self.last_url = ""
        self.last_json: dict[str, Any] = {}

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int) -> Response:
        self.calls += 1
        self.last_url = url
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


def _adapter(http: object, model: str = REQUESTED_MODEL, endpoint: str = ENDPOINT) -> NvidiaNimAdapter:
    return NvidiaNimAdapter(
        http,
        model,
        SECRET,
        cloud_guard=AllowingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
        endpoint=endpoint,
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


async def test_nim_translate_maps_native_chat_payload_and_pinned_api_kind(translation_request: TranslationRequest) -> None:
    http = Http()

    result = await _adapter(http).translate(translation_request)

    assert result.target_text == TRANSLATED
    assert result.provider == PROVIDER == "nvidia-nim"
    assert result.model == REPORTED_MODEL
    assert result.provider_version == "chat.completions"
    assert _adapter(http).capabilities()["api_kind"] == API_KIND
    assert http.calls == 1
    assert http.last_url == ENDPOINT
    assert http.last_timeout == translation_request.context.timeout_seconds
    assert http.last_headers["Authorization"] == f"Bearer {SECRET}"
    assert http.last_headers["Content-Type"] == "application/json"
    assert http.last_json["model"] == REQUESTED_MODEL
    # OpenAI-style NIM chat payload: model + messages, and nothing the caller
    # never configured (no invented max_tokens cap).
    assert set(http.last_json) == {"model", "messages"}
    assert [message["role"] for message in http.last_json["messages"]] == ["system", "user"]
    user_content = http.last_json["messages"][-1]["content"]
    assert "她打开门" in user_content
    assert "门 -> cửa" in user_content
    assert "zh-CN" in user_content
    assert "vi-VN" in user_content
    assert "seg-1" in user_content


async def test_nim_maps_usage_tokens_and_request_id(translation_request: TranslationRequest) -> None:
    http = Http()

    result = await _adapter(http).translate(translation_request)

    assert result.usage[0].unit == UsageUnit.INPUT_TOKEN.value
    assert result.usage[0].measured_units == 15
    assert result.usage[0].provider_request_id == "chatcmpl-nim-1"
    assert result.usage[1].unit == UsageUnit.OUTPUT_TOKEN.value
    assert result.usage[1].measured_units == 11
    assert result.usage[1].provider_request_id == "chatcmpl-nim-1"


async def test_nim_reports_provider_actual_model_instead_of_requested(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = _adapter(http, model="meta/llama-3.1-70b-instruct")

    result = await adapter.translate(translation_request)

    assert result.model == REPORTED_MODEL
    assert result.model != REQUESTED_MODEL
    assert adapter.last_model_source == MODEL_SOURCE_PROVIDER
    # The requested model is what was sent; the substitution is only surfaced,
    # never hidden and never silently applied on the wire.
    assert http.last_json["model"] == REQUESTED_MODEL


@pytest.mark.parametrize("reported", [None, "", "   ", 42, ["model"], {"model": "x"}])
async def test_nim_records_requested_model_as_fallback_when_provider_omits_it(
    translation_request: TranslationRequest,
    reported: object,
) -> None:
    http = Http(Response(_chat_body(model=reported)))
    adapter = _adapter(http)

    result = await adapter.translate(translation_request)

    # Capability unknown fails closed: the configured model is used, and the
    # fallback is recorded explicitly instead of being presented as observed.
    assert result.model == REQUESTED_MODEL
    assert adapter.last_model_source == MODEL_SOURCE_REQUESTED


@pytest.mark.parametrize(
    "usage",
    [
        None,
        "15",
        {"prompt_tokens": "15", "completion_tokens": 11.5},
        {"prompt_tokens": -4, "completion_tokens": True},
        [],
    ],
)
async def test_nim_usage_defaults_to_zero_without_provider_numbers(
    translation_request: TranslationRequest,
    usage: object,
) -> None:
    http = Http(Response(_chat_body(usage=usage)))

    result = await _adapter(http).translate(translation_request)

    assert result.usage[0].measured_units == 0
    assert result.usage[1].measured_units == 0


async def test_nim_keeps_the_usable_usage_field_and_zeroes_only_the_unusable_one(
    translation_request: TranslationRequest,
) -> None:
    http = Http(Response(_chat_body(usage={"prompt_tokens": 15, "completion_tokens": "11"})))

    result = await _adapter(http).translate(translation_request)

    assert result.usage[0].measured_units == 15
    assert result.usage[1].measured_units == 0


async def test_nim_translate_opens_no_socket(
    translation_request: TranslationRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls = install_socket_tripwire(monkeypatch)
    http = Http()

    result = await _adapter(http).translate(translation_request)

    assert result.target_text == TRANSLATED
    assert network_calls == []


# --- guard -------------------------------------------------------------------


async def test_nim_guard_missing_blocks_before_any_http(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = NvidiaNimAdapter(http, REQUESTED_MODEL, SECRET)

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CLOUD_GUARD_REQUIRED",)
    assert http.calls == 0


async def test_nim_guard_context_missing_blocks_before_any_http(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = NvidiaNimAdapter(http, REQUESTED_MODEL, SECRET, cloud_guard=AllowingGuard())

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CLOUD_GUARD_CONTEXT_REQUIRED",)
    assert http.calls == 0


async def test_nim_denied_guard_blocks_before_any_http(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = NvidiaNimAdapter(
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


async def test_nim_guard_precedes_source_validation(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = NvidiaNimAdapter(http, REQUESTED_MODEL, SECRET)

    with pytest.raises(CloudCallBlocked):
        await adapter.translate(_request(source_text="   "))

    assert http.calls == 0


async def test_nim_rejects_empty_source_before_any_http(translation_request: TranslationRequest) -> None:
    http = Http()

    with pytest.raises(ValueError, match=SOURCE_REQUIRED):
        await _adapter(http).translate(_request(source_text="   "))

    assert http.calls == 0


async def test_nim_empty_credential_fails_before_any_dispatch(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = NvidiaNimAdapter(
        http,
        REQUESTED_MODEL,
        "   ",
        cloud_guard=AllowingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(ProviderTransportError) as exc:
        await adapter.translate(translation_request)

    assert exc.value.code == "PROVIDER_AUTH_MISSING"
    assert exc.value.billing_state is BillingState.NOT_SENT
    assert http.calls == 0


# --- error normalisation -----------------------------------------------------


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
async def test_nim_http_error_matrix_maps_billing_state_without_retry(
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
    # Exactly one dispatch, including the retryable 429/5xx codes: retry policy
    # belongs to the J02 policy layer, never to the adapter.
    assert http.calls == 1
    assert SECRET not in str(exc.value)


async def test_nim_timeout_after_send_is_billing_unknown_without_retry(
    translation_request: TranslationRequest,
) -> None:
    http = RaisingHttp(TimeoutError("provider never answered"))

    with pytest.raises(ProviderBillingUnknown) as exc:
        await _adapter(http).translate(translation_request)

    assert str(exc.value) == BILLING_UNKNOWN == "NIM_BILLING_UNKNOWN"
    assert http.calls == 1
    assert http.last_url == ENDPOINT
    assert "她打开门" in http.last_json["messages"][-1]["content"]
    assert SECRET not in repr(http.last_json)
    assert SECRET not in str(exc.value)


@pytest.mark.parametrize(
    "exception",
    [httpx.ReadTimeout("read timed out"), httpx.ConnectError("connect unclear"), httpx.RemoteProtocolError("lost")],
)
async def test_nim_transport_error_after_send_is_billing_unknown(
    translation_request: TranslationRequest,
    exception: Exception,
) -> None:
    http = RaisingHttp(exception)

    with pytest.raises(ProviderBillingUnknown):
        await _adapter(http).translate(translation_request)

    assert http.calls == 1


async def test_nim_json_decode_failure_fails_closed_as_unknown(translation_request: TranslationRequest) -> None:
    http = Http(Response(ValueError(f"secret={SECRET}")))

    with pytest.raises(ProviderTransportError) as exc:
        await _adapter(http).translate(translation_request)

    assert exc.value.code == "MALFORMED_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN
    assert SECRET not in str(exc.value)


@pytest.mark.parametrize("body", [[{"model": REPORTED_MODEL}], "not-json", 17, None])
async def test_nim_non_object_body_is_malformed(translation_request: TranslationRequest, body: object) -> None:
    http = Http(Response(body))

    with pytest.raises(ProviderTransportError) as exc:
        await _adapter(http).translate(translation_request)

    assert exc.value.code == "MALFORMED_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN


async def test_nim_missing_choices_is_empty_response(translation_request: TranslationRequest) -> None:
    http = Http(Response(_chat_body_without_choices()))

    with pytest.raises(ProviderTransportError) as exc:
        await _adapter(http).translate(translation_request)

    assert exc.value.code == "EMPTY_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN


@pytest.mark.parametrize(
    "choices",
    [[], "not-a-list", {}, [{}], [{"message": "not-a-dict"}], [{"message": {"content": "   "}}], [{"message": {"content": None}}]],
)
async def test_nim_unusable_choices_are_empty_response(
    translation_request: TranslationRequest,
    choices: object,
) -> None:
    http = Http(Response(_chat_body(choices=choices, usage=None)))

    with pytest.raises(ProviderTransportError) as exc:
        await _adapter(http).translate(translation_request)

    assert exc.value.code == "EMPTY_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN


# --- endpoint policy ---------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://integrate.api.nvidia.com/v1/chat/completions",
        "ftp://nim.internal.example/v1/chat/completions",
        "file:///etc/passwd",
        "ws://nim.internal.example/v1/chat/completions",
        "https://",
        "https://   ",
        "//nim.internal.example/v1/chat/completions",
        "nim.internal.example/v1/chat/completions",
        "",
        "   ",
        None,
        42,
        ["https://nim.internal.example/v1/chat/completions"],
        "https://user:secret@nim.internal.example/v1/chat/completions",
        "https://nim.internal.example:not-a-port/v1/chat/completions",
    ],
)
def test_nim_rejects_unsupported_endpoints_before_any_request(endpoint: object) -> None:
    http = Http()

    with pytest.raises(ValueError, match=ENDPOINT_UNSUPPORTED):
        NvidiaNimAdapter(
            http,
            REQUESTED_MODEL,
            SECRET,
            cloud_guard=AllowingGuard(),
            project_id="project-001",
            provider_profile_id="profile-001",
            endpoint=endpoint,  # type: ignore[arg-type]
        )

    assert http.calls == 0


def test_nim_endpoint_validator_keeps_a_self_hosted_https_endpoint_verbatim() -> None:
    assert canonical_nim_endpoint(SELF_HOSTED_ENDPOINT) == SELF_HOSTED_ENDPOINT
    assert canonical_nim_endpoint(f"  {SELF_HOSTED_ENDPOINT}  ") == SELF_HOSTED_ENDPOINT
    assert canonical_nim_endpoint(ENDPOINT) == ENDPOINT


async def test_nim_uses_the_configured_endpoint_and_assumes_no_host_equivalence(
    translation_request: TranslationRequest,
) -> None:
    hosted = Http()
    self_hosted = Http()

    await _adapter(hosted, endpoint=ENDPOINT).translate(translation_request)
    await _adapter(self_hosted, endpoint=SELF_HOSTED_ENDPOINT).translate(translation_request)

    assert hosted.last_url == ENDPOINT
    assert self_hosted.last_url == SELF_HOSTED_ENDPOINT
    assert hosted.last_url != self_hosted.last_url
    assert _adapter(Http(), endpoint=SELF_HOSTED_ENDPOINT).endpoint == SELF_HOSTED_ENDPOINT
    assert _adapter(Http(), endpoint=SELF_HOSTED_ENDPOINT).capabilities()["endpoint_scheme"] == "https"


# --- credential handling and capability --------------------------------------


async def test_nim_never_places_or_returns_the_api_key(translation_request: TranslationRequest) -> None:
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


def test_nim_capabilities_pin_provider_model_and_api_kind() -> None:
    adapter = _adapter(Http(), endpoint=SELF_HOSTED_ENDPOINT)

    capabilities = adapter.capabilities()

    assert capabilities["provider"] == PROVIDER
    assert capabilities["model"] == REQUESTED_MODEL
    assert capabilities["api_kind"] == API_KIND
    assert capabilities["network"] is True
    # No discovery call is made against a NIM endpoint, so the capability is
    # declared, never verified: it fails closed instead of over-claiming.
    assert capabilities["capability_verified"] is False
    assert SECRET not in json.dumps(capabilities)
    assert adapter.capabilities() == capabilities


def test_nim_model_source_starts_as_a_declared_fallback() -> None:
    adapter = _adapter(Http())

    assert adapter.last_model_source == MODEL_SOURCE_REQUESTED
    assert adapter.endpoint == ENDPOINT
