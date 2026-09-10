"""Offline contract tests for the Groq adapter (plan X01).

Every case is hermetic: a stub HTTP client stands in for the network and no
socket is opened, so the native chat-completions payload, the credential
placement, the usage/model mapping and the error/billing-state matrix are
asserted against real adapter behaviour rather than a live provider.

NOT RUN here (and not claimed): no live Groq account, key, region or terms were
exercised, so the free-plan/quota figures in the provider research stay
unverified and the adapter keeps live_verified=False.
"""

from __future__ import annotations

import copy
import json
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest

from app.contracts import OperationContext, TranslationRequest, Usage, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked, CloudCallDecision
from app.modules.execution.contracts import BillingState
from app.providers.groq import (
    ENDPOINT,
    PROVIDER,
    PROVIDER_VERSION,
    SOURCE_REQUIRED,
    GroqAdapter,
    ProviderBillingUnknown,
)
from app.providers.transport import ProviderTransportError
from tests.providers.contract_suite import install_socket_tripwire


SECRET = "groq-key-do-not-leak-001"
REQUESTED_MODEL = "llama-3.3-70b-versatile"
REPORTED_MODEL = "openai/gpt-oss-120b"
TRANSLATED = "Cô ấy mở cửa."


def _request(**overrides: object) -> TranslationRequest:
    payload: dict[str, object] = {
        "context": OperationContext(
            operation_id="op-groq-1",
            cache_key="cache-groq-1",
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
        "id": "chatcmpl-groq-1",
        "model": REPORTED_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": TRANSLATED},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 13, "completion_tokens": 9, "total_tokens": 22},
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
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def evaluate(self, **kwargs: object) -> CloudCallDecision:
        self.calls.append(dict(kwargs))
        return CloudCallDecision(
            allowed=True,
            cloud_consent_id=str(kwargs["cloud_consent_id"]),
            authorization_id=str(kwargs["budget_authorization_id"]),
            rate_card_ids=("rate-card-1",),
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


def _adapter(
    http: object,
    *,
    model: str = REQUESTED_MODEL,
    api_key: str = SECRET,
    cloud_guard: object | None = None,
    project_id: str | None = "project-1",
    provider_profile_id: str | None = "profile-1",
    endpoint: str = ENDPOINT,
) -> GroqAdapter:
    return GroqAdapter(
        http,
        model,
        api_key,
        cloud_guard=cloud_guard if cloud_guard is not None else AllowingGuard(),
        project_id=project_id,
        provider_profile_id=provider_profile_id,
        endpoint=endpoint,
    )


async def test_groq_sends_the_minimal_openai_chat_payload(
    translation_request: TranslationRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls = install_socket_tripwire(monkeypatch)
    http = Http()
    adapter = _adapter(http)
    before = copy.deepcopy(translation_request)

    result = await adapter.translate(translation_request)

    assert network_calls == []
    assert http.calls == 1
    assert http.last_url == ENDPOINT
    assert http.last_timeout == 17
    # Native shape: only the two documented fields, no guessed OpenAI extras.
    assert set(http.last_json) == {"model", "messages"}
    assert http.last_json["model"] == REQUESTED_MODEL
    roles = [message["role"] for message in http.last_json["messages"]]
    assert roles == ["system", "user"]
    user_content = http.last_json["messages"][1]["content"]
    assert user_content == (
        "Source language: zh-CN\nTarget language: vi-VN\n"
        "Glossary:\n门 -> cửa\n\n[seg-1] 她打开门。"
    )
    assert "Translate faithfully" in http.last_json["messages"][0]["content"]
    assert translation_request == before
    assert result.target_text == TRANSLATED
    assert result.provider == PROVIDER
    assert result.model == REPORTED_MODEL
    assert result.provider_version == PROVIDER_VERSION
    assert adapter.last_finish_reason == "stop"


async def test_groq_maps_usage_tokens_and_request_id(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = _adapter(http)

    result = await adapter.translate(translation_request)

    assert result.usage == (
        Usage(UsageUnit.INPUT_TOKEN.value, 13, "chatcmpl-groq-1"),
        Usage(UsageUnit.OUTPUT_TOKEN.value, 9, "chatcmpl-groq-1"),
    )


async def test_groq_reports_zero_usage_when_the_provider_reports_none(
    translation_request: TranslationRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = Http(Response(_chat_body(usage={}, id=None)))
    adapter = _adapter(http)

    result = await adapter.translate(translation_request)

    assert result.usage == (
        Usage(UsageUnit.INPUT_TOKEN.value, 0, None),
        Usage(UsageUnit.OUTPUT_TOKEN.value, 0, None),
    )


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (None, [0, 0]),
        ({}, [0, 0]),
        ({"prompt_tokens": "13", "completion_tokens": 9}, [0, 9]),
        ({"prompt_tokens": True, "completion_tokens": -3}, [0, 0]),
        ({"prompt_tokens": 2.5, "completion_tokens": 9}, [0, 9]),
        ({"prompt_tokens": 21, "completion_tokens": None}, [21, 0]),
    ],
)
async def test_groq_never_invents_usage_numbers(
    translation_request: TranslationRequest,
    usage: Any,
    expected: list[int],
) -> None:
    http = Http(Response(_chat_body(usage=usage)))
    adapter = _adapter(http)

    result = await adapter.translate(translation_request)

    # Only what the provider actually reported: anything else is a zero, never
    # a number inferred from the request.
    assert [entry.measured_units for entry in result.usage] == expected


async def test_groq_records_the_model_the_provider_actually_served(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = _adapter(http)

    result = await adapter.translate(translation_request)

    assert http.last_json["model"] == REQUESTED_MODEL
    assert result.model == REPORTED_MODEL
    assert result.model != REQUESTED_MODEL


@pytest.mark.parametrize("reported", [None, "", "   ", 7])
async def test_groq_uses_the_requested_model_only_when_none_is_reported(
    translation_request: TranslationRequest,
    reported: Any,
) -> None:
    http = Http(Response(_chat_body(model=reported)))
    adapter = _adapter(http)

    result = await adapter.translate(translation_request)

    assert result.model == REQUESTED_MODEL
    assert result.model != reported


async def test_groq_reports_no_finish_reason_when_the_provider_sends_none(
    translation_request: TranslationRequest,
) -> None:
    http = Http(Response(_chat_body(choices=[{"index": 0, "message": {"content": TRANSLATED}}])))
    adapter = _adapter(http)

    result = await adapter.translate(translation_request)

    assert result.target_text == TRANSLATED
    assert adapter.last_finish_reason is None


async def test_groq_guard_receives_the_request_context_before_dispatch(
    translation_request: TranslationRequest,
) -> None:
    guard = AllowingGuard()
    http = Http()
    adapter = _adapter(http, cloud_guard=guard)

    await adapter.translate(translation_request)

    assert len(guard.calls) == 1
    recorded = guard.calls[0]
    assert recorded["project_id"] == "project-1"
    assert recorded["provider_profile_id"] == "profile-1"
    assert recorded["operation_id"] == "op-groq-1"
    assert recorded["stage"] == "TRANSLATE"
    assert recorded["estimated_usage"] == (Usage(UsageUnit.INPUT_TOKEN.value, 42),)
    assert recorded["cloud_consent_id"] == "consent-1"
    assert recorded["budget_authorization_id"] == "auth-1"
    assert http.calls == 1


async def test_groq_blocks_without_cloud_guard_before_http(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = GroqAdapter(http, REQUESTED_MODEL, SECRET)

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CLOUD_GUARD_REQUIRED",)
    assert http.calls == 0


async def test_groq_blocks_when_the_guard_denies_before_http(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = _adapter(http, cloud_guard=DenyingGuard())

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CONSENT_NOT_GRANTED",)
    assert http.calls == 0


async def test_groq_requires_guard_context_before_http(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = GroqAdapter(http, REQUESTED_MODEL, SECRET, cloud_guard=AllowingGuard())

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CLOUD_GUARD_CONTEXT_REQUIRED",)
    assert http.calls == 0


@pytest.mark.parametrize("source_text", ["", " ", "\n\t"])
async def test_groq_rejects_an_empty_source_before_http(
    translation_request: TranslationRequest,
    source_text: str,
) -> None:
    http = Http()
    adapter = _adapter(http)

    with pytest.raises(ValueError, match=SOURCE_REQUIRED):
        await adapter.translate(_request(source_text=source_text))

    assert http.calls == 0


async def test_groq_missing_credential_fails_before_http(translation_request: TranslationRequest) -> None:
    http = Http()
    adapter = _adapter(http, api_key="")

    with pytest.raises(ProviderTransportError) as exc:
        await adapter.translate(translation_request)

    assert exc.value.code == "PROVIDER_AUTH_MISSING"
    assert exc.value.billing_state is BillingState.NOT_SENT
    assert http.calls == 0


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
async def test_groq_http_error_matrix_uses_shared_transport(
    translation_request: TranslationRequest,
    status_code: int,
    billing_state: BillingState,
    retryable: bool,
) -> None:
    http = StatusHttp(status_code)
    adapter = _adapter(http)

    with pytest.raises(ProviderTransportError) as exc:
        await adapter.translate(translation_request)

    assert exc.value.code == f"HTTP_{status_code}"
    assert exc.value.billing_state is billing_state
    assert exc.value.retryable is retryable
    # The adapter never retries; retry policy lives in the J02 policy layer.
    assert http.calls == 1
    assert SECRET not in repr(exc.value)


async def test_groq_timeout_after_send_is_billing_unknown_without_retry(
    translation_request: TranslationRequest,
) -> None:
    http = RaisingHttp(TimeoutError("provider timed out after dispatch"))
    adapter = _adapter(http)

    with pytest.raises(ProviderBillingUnknown) as exc:
        await adapter.translate(translation_request)

    assert str(exc.value) == "GROQ_BILLING_UNKNOWN"
    assert http.calls == 1
    assert http.last_json["model"] == REQUESTED_MODEL
    assert SECRET not in repr(exc.value)


@pytest.mark.parametrize(
    "exception",
    [
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.ConnectError("transport unclear"),
        httpx.WriteError("write failed"),
    ],
)
async def test_groq_httpx_transport_failure_after_dispatch_is_billing_unknown(
    translation_request: TranslationRequest,
    exception: Exception,
) -> None:
    http = RaisingHttp(exception)
    adapter = _adapter(http)

    with pytest.raises(ProviderBillingUnknown) as exc:
        await adapter.translate(translation_request)

    assert str(exc.value) == "GROQ_BILLING_UNKNOWN"
    assert http.calls == 1


async def test_groq_malformed_json_is_reported_as_malformed(translation_request: TranslationRequest) -> None:
    http = Http(Response(ValueError(f"secret={SECRET}")))
    adapter = _adapter(http)

    with pytest.raises(ProviderTransportError) as exc:
        await adapter.translate(translation_request)

    assert exc.value.code == "MALFORMED_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN
    assert exc.value.status_code == 200
    assert SECRET not in repr(exc.value)


async def test_groq_non_object_response_is_malformed(translation_request: TranslationRequest) -> None:
    http = Http(Response(["not", "an", "object"]))
    adapter = _adapter(http)

    with pytest.raises(ProviderTransportError) as exc:
        await adapter.translate(translation_request)

    assert exc.value.code == "MALFORMED_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": None},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "   "}}]},
        {"choices": [{"message": {"content": 42}}]},
    ],
    ids=[
        "no-choices",
        "choices-null",
        "choices-empty",
        "choice-without-message",
        "message-null",
        "content-empty",
        "content-blank",
        "content-not-a-string",
    ],
)
async def test_groq_empty_response_fails_closed(translation_request: TranslationRequest, body: dict[str, Any]) -> None:
    http = Http(Response(body))
    adapter = _adapter(http)

    with pytest.raises(ProviderTransportError) as exc:
        await adapter.translate(translation_request)

    assert exc.value.code == "EMPTY_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN
    assert exc.value.status_code == 200


async def test_groq_never_places_the_credential_outside_the_authorization_header(
    translation_request: TranslationRequest,
) -> None:
    http = Http()
    adapter = _adapter(http)

    result = await adapter.translate(translation_request)

    assert http.last_headers["Authorization"] == f"Bearer {SECRET}"
    assert http.last_headers["Content-Type"] == "application/json"
    leaked_headers = {
        name: value
        for name, value in http.last_headers.items()
        if name != "Authorization" and SECRET in str(value)
    }
    assert leaked_headers == {}
    parsed = urlparse(http.last_url)
    assert parsed.query == ""
    assert SECRET not in http.last_url
    assert SECRET not in json.dumps(http.last_json)
    assert SECRET not in repr(result)
    assert SECRET not in str(result)
    assert SECRET not in repr(result.usage)
    assert SECRET not in repr(adapter)
    assert SECRET not in json.dumps(adapter.capabilities())


async def test_groq_never_leaks_the_credential_through_failure_paths(
    translation_request: TranslationRequest,
) -> None:
    failures: list[BaseException] = []
    for http in (
        RaisingHttp(TimeoutError("timed out after dispatch")),
        StatusHttp(500),
        Http(Response(ValueError(f"secret={SECRET}"))),
    ):
        adapter = _adapter(http)
        with pytest.raises((ProviderBillingUnknown, ProviderTransportError)) as exc:
            await adapter.translate(translation_request)
        failures.append(exc.value)

    assert len(failures) == 3
    for failure in failures:
        assert SECRET not in repr(failure)
        assert SECRET not in str(failure)
        assert SECRET not in str(failure.args)


def test_groq_capabilities_describe_the_chat_surface_without_a_free_or_unlimited_claim() -> None:
    adapter = _adapter(Http(), model=REQUESTED_MODEL)

    capabilities = adapter.capabilities()

    assert capabilities["provider"] == PROVIDER
    assert capabilities["model"] == REQUESTED_MODEL
    assert capabilities["api_kind"] == "chat"
    assert capabilities["network"] is True
    assert capabilities["live_verified"] is False
    assert capabilities["billing"] == "account-dependent"
    assert set(capabilities) == {"provider", "model", "api_kind", "network", "live_verified", "billing"}
    rendered = json.dumps(capabilities).lower()
    assert "free" not in rendered
    assert "unlimited" not in rendered
    assert "rpm" not in rendered
    assert "tpd" not in rendered
    assert SECRET not in json.dumps(capabilities)
    assert adapter.capabilities() == capabilities


def test_groq_repr_redacts_the_credential() -> None:
    adapter = _adapter(Http())

    rendered = repr(adapter)

    assert SECRET not in rendered
    assert "credential=<redacted>" in rendered
    assert REQUESTED_MODEL in rendered
    assert ENDPOINT in rendered
