from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.contracts import OperationContext, TranslationRequest, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked, CloudCallDecision
from app.modules.execution.contracts import BillingState
from app.providers.cloudflare import (
    API_REPORTED_FAILURE,
    CloudflareWorkersAiAdapter,
    ProviderBillingUnknown,
)
from app.providers.transport import ProviderTransportError
from tests.providers.contract_suite import install_socket_tripwire


ACCOUNT_ID = "acct0f1e2d3c4b5a69788"
MODEL = "@cf/meta/llama-3.1-8b-instruct"
API_TOKEN = "cf-token-do-not-leak"
EXPECTED_ENDPOINT = (
    f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{MODEL}"
)
TRANSLATED = "Cô ấy mở cửa."


def _ok_body(**result_overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"response": TRANSLATED}
    result.update(result_overrides)
    return {"result": result, "success": True, "errors": [], "messages": []}


def _adapter(http: Any, **overrides: Any) -> CloudflareWorkersAiAdapter:
    kwargs: dict[str, Any] = {
        "account_id": ACCOUNT_ID,
        "cloud_guard": AllowingGuard(),
        "project_id": "project-001",
        "provider_profile_id": "profile-001",
    }
    kwargs.update(overrides)
    return CloudflareWorkersAiAdapter(http, kwargs.pop("model", MODEL), API_TOKEN, **kwargs)


@pytest.fixture
def translation_request() -> TranslationRequest:
    return TranslationRequest(
        context=OperationContext(
            operation_id="translate-segment-001",
            cache_key="cache-001",
            timeout_seconds=12,
            estimated_units=42,
            budget_authorization_id="auth-001",
            cloud_consent_id="consent-001",
        ),
        source_segment_id="seg-001",
        source_text="她打开门。",
        source_language="zh-CN",
        target_language="vi-VN",
        terms=(("门", "cửa"),),
        tm_list=(("她打开门。", "Cô ấy mở cửa."),),
        domain_instruction="natural",
        story_memory=("Nhân vật chính đang bị truy đuổi.",),
    )


@pytest.mark.asyncio
async def test_cloudflare_posts_native_chat_body_to_the_account_scoped_run_path(
    translation_request: TranslationRequest,
) -> None:
    http = Http(_ok_body())
    adapter = _adapter(http)

    result = await adapter.translate(translation_request)

    assert result.target_text == TRANSLATED
    assert result.provider == "cloudflare-workers-ai"
    assert result.model == MODEL
    assert result.provider_version == "v4"
    assert result.usage[0].unit == UsageUnit.INPUT_TOKEN.value
    assert result.usage[0].measured_units == 0
    assert result.usage[1].unit == UsageUnit.OUTPUT_TOKEN.value
    assert result.usage[1].measured_units == 0

    # Workers AI takes the model from the run path, not from a body field.
    assert http.last_url == EXPECTED_ENDPOINT
    payload = http.last_json
    assert list(payload) == ["messages"]
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]
    assert "model" not in payload
    assert "account_id" not in payload
    user_content = payload["messages"][-1]["content"]
    assert "她打开门" in user_content
    assert "门 -> cửa" in user_content
    assert "zh-CN" in user_content
    assert "vi-VN" in user_content

    assert list(http.last_headers) == ["Authorization", "Content-Type"]
    assert http.last_headers["Authorization"] == f"Bearer {API_TOKEN}"
    assert http.last_headers["Content-Type"] == "application/json"
    assert http.last_timeout == translation_request.context.timeout_seconds
    assert http.calls == 1
    assert adapter.capabilities() == {
        "provider": "cloudflare-workers-ai",
        "model": MODEL,
        "api_kind": "chat",
        "network": True,
    }


@pytest.mark.asyncio
async def test_cloudflare_reads_openai_compatible_result_usage_and_actual_model(
    translation_request: TranslationRequest,
) -> None:
    http = Http(
        {
            "result": {
                "choices": [{"message": {"content": TRANSLATED}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                "model": "vendor/actual-model-v2",
            },
            "success": True,
            "errors": [],
        }
    )
    adapter = _adapter(http)

    result = await adapter.translate(translation_request)

    assert result.target_text == TRANSLATED
    # The provider-reported model wins over the requested one.
    assert result.model == "vendor/actual-model-v2"
    assert result.usage[0].measured_units == 11
    assert result.usage[1].measured_units == 7
    assert result.usage[0].provider_request_id is None
    assert result.usage[1].provider_request_id is None


@pytest.mark.asyncio
async def test_cloudflare_falls_back_to_the_requested_model_when_unreported(
    translation_request: TranslationRequest,
) -> None:
    http = Http(_ok_body())
    adapter = _adapter(http)

    result = await adapter.translate(translation_request)

    # No model echo in the envelope: the requested model is reported, never a
    # guessed one.
    assert result.model == MODEL


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result_overrides",
    [
        {},
        {"usage": {}},
        {"usage": {"prompt_tokens": "11", "completion_tokens": -3}},
        {"usage": {"input_tokens": "11", "output_tokens": None}},
    ],
)
async def test_cloudflare_reports_zero_tokens_instead_of_inventing_usage(
    translation_request: TranslationRequest,
    result_overrides: dict[str, Any],
) -> None:
    http = Http(_ok_body(**result_overrides))
    adapter = _adapter(http)

    result = await adapter.translate(translation_request)

    assert result.usage[0].measured_units == 0
    assert result.usage[1].measured_units == 0


@pytest.mark.asyncio
async def test_cloudflare_guard_is_required_before_http(
    translation_request: TranslationRequest,
) -> None:
    http = Http(_ok_body())
    adapter = CloudflareWorkersAiAdapter(http, MODEL, API_TOKEN, account_id=ACCOUNT_ID)

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CLOUD_GUARD_REQUIRED",)
    assert http.calls == 0


@pytest.mark.asyncio
async def test_cloudflare_denied_guard_blocks_before_http(
    translation_request: TranslationRequest,
) -> None:
    http = Http(_ok_body())
    adapter = CloudflareWorkersAiAdapter(
        http,
        MODEL,
        API_TOKEN,
        account_id=ACCOUNT_ID,
        cloud_guard=DenyingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CONSENT_NOT_GRANTED",)
    assert http.calls == 0


@pytest.mark.asyncio
async def test_cloudflare_guard_precedes_source_validation(
    translation_request: TranslationRequest,
) -> None:
    http = Http(_ok_body())
    adapter = CloudflareWorkersAiAdapter(http, MODEL, API_TOKEN, account_id=ACCOUNT_ID)
    empty = replace_source_text(translation_request, " ")

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(empty)

    # The security guard runs first: an unguarded call is blocked, never a
    # plain validation error that could look like a safe no-op.
    assert exc.value.reasons == ("CLOUD_GUARD_REQUIRED",)
    assert http.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("source_text", ["", " ", "\n\t"])
async def test_cloudflare_rejects_empty_source_before_http(
    translation_request: TranslationRequest,
    source_text: str,
) -> None:
    http = Http(_ok_body())
    adapter = _adapter(http)
    empty = replace_source_text(translation_request, source_text)

    with pytest.raises(ValueError, match="CLOUDFLARE_SOURCE_REQUIRED"):
        await adapter.translate(empty)

    assert http.calls == 0


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
async def test_cloudflare_http_error_matrix_uses_shared_transport(
    translation_request: TranslationRequest,
    status_code: int,
    billing_state: BillingState,
    retryable: bool,
) -> None:
    http = StatusHttp(status_code, _ok_body())
    adapter = _adapter(http)

    with pytest.raises(ProviderTransportError) as exc:
        await adapter.translate(translation_request)

    assert exc.value.code == f"HTTP_{status_code}"
    assert exc.value.billing_state is billing_state
    assert exc.value.retryable is retryable
    assert exc.value.status_code == status_code
    # Provider bodies can quote the account and the submitted text.
    assert API_TOKEN not in str(exc.value)
    assert ACCOUNT_ID not in str(exc.value)
    assert http.calls == 1


@pytest.mark.asyncio
async def test_cloudflare_timeout_after_send_marks_billing_unknown_without_retry(
    translation_request: TranslationRequest,
) -> None:
    http = RaisingHttp(TimeoutError("provider timed out after request dispatch"))
    adapter = _adapter(http)

    with pytest.raises(ProviderBillingUnknown) as exc:
        await adapter.translate(translation_request)

    assert str(exc.value) == "CLOUDFLARE_BILLING_UNKNOWN"
    assert http.calls == 1
    assert "她打开门" in http.last_json["messages"][-1]["content"]
    assert API_TOKEN not in repr(http.last_json)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception",
    [httpx.ReadTimeout("read timed out"), httpx.ConnectError("transport unclear")],
)
async def test_cloudflare_httpx_transport_failure_after_send_marks_billing_unknown(
    translation_request: TranslationRequest,
    exception: Exception,
) -> None:
    http = RaisingHttp(exception)
    adapter = _adapter(http)

    with pytest.raises(ProviderBillingUnknown):
        await adapter.translate(translation_request)

    assert http.calls == 1


@pytest.mark.asyncio
async def test_cloudflare_malformed_json_uses_shared_transport_error(
    translation_request: TranslationRequest,
) -> None:
    http = MalformedJsonHttp()
    adapter = _adapter(http)

    with pytest.raises(ProviderTransportError) as exc:
        await adapter.translate(translation_request)

    assert exc.value.code == "MALFORMED_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN
    assert exc.value.retryable is False
    assert API_TOKEN not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        ["not", "an", "envelope"],
        {"result": {"response": TRANSLATED}},
        {"result": {"response": TRANSLATED}, "success": "true"},
        {"result": {"response": TRANSLATED}, "success": None},
        {"success": True},
        {"result": "raw text outside the envelope", "success": True},
    ],
)
async def test_cloudflare_invalid_envelope_shape_is_never_a_success(
    translation_request: TranslationRequest,
    body: Any,
) -> None:
    http = Http(body)
    adapter = _adapter(http)

    with pytest.raises(ProviderTransportError) as exc:
        await adapter.translate(translation_request)

    assert exc.value.code == "MALFORMED_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN
    assert exc.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {},
        {"response": ""},
        {"response": "   "},
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {}}]},
    ],
)
async def test_cloudflare_empty_result_is_never_a_success(
    translation_request: TranslationRequest,
    result: dict[str, Any],
) -> None:
    http = Http({"result": result, "success": True, "errors": []})
    adapter = _adapter(http)

    with pytest.raises(ProviderTransportError) as exc:
        await adapter.translate(translation_request)

    assert exc.value.code == "EMPTY_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN
    assert API_TOKEN not in str(exc.value)


@pytest.mark.asyncio
async def test_cloudflare_envelope_failure_is_normalized_without_echoing_the_body(
    translation_request: TranslationRequest,
) -> None:
    http = Http(
        {
            "result": None,
            "success": False,
            "errors": [
                {"code": 7000, "message": f"account {ACCOUNT_ID} token {API_TOKEN} rejected"}
            ],
        }
    )
    adapter = _adapter(http)

    with pytest.raises(ProviderTransportError) as exc:
        await adapter.translate(translation_request)

    assert exc.value.code == API_REPORTED_FAILURE
    assert exc.value.billing_state is BillingState.UNKNOWN
    assert exc.value.retryable is False
    assert exc.value.status_code == 200
    assert API_TOKEN not in str(exc.value)
    assert ACCOUNT_ID not in str(exc.value)


@pytest.mark.asyncio
async def test_cloudflare_never_places_credentials_in_the_payload_or_the_result(
    translation_request: TranslationRequest,
) -> None:
    http = Http(_ok_body())
    adapter = _adapter(http)

    result = await adapter.translate(translation_request)

    assert API_TOKEN not in json.dumps(http.last_json)
    assert API_TOKEN not in http.last_url
    assert ACCOUNT_ID not in json.dumps(http.last_json)
    assert API_TOKEN not in repr(result)
    for field in result.__dataclass_fields__:
        assert API_TOKEN not in str(getattr(result, field))
    for usage in result.usage:
        assert API_TOKEN not in repr(usage)

    capabilities = adapter.capabilities()
    assert API_TOKEN not in json.dumps(capabilities)
    assert ACCOUNT_ID not in json.dumps(capabilities)
    assert "account" not in "".join(capabilities)

    # Traces and logs must not carry the token or the account id either.
    assert API_TOKEN not in repr(adapter)
    assert ACCOUNT_ID not in repr(adapter)
    assert API_TOKEN not in str(adapter.__dict__.get("model"))


@pytest.mark.asyncio
async def test_cloudflare_translate_touches_no_socket(
    translation_request: TranslationRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_calls = install_socket_tripwire(monkeypatch)
    http = Http(_ok_body())
    adapter = _adapter(http)

    result = await adapter.translate(translation_request)

    assert result.target_text == TRANSLATED
    assert http.calls == 1
    assert network_calls == []


@pytest.mark.parametrize(
    "model",
    [
        "@cf/baai/bge-base-en-v1.5",
        "@cf/baai/bge-reranker-base",
        "@cf/openai/whisper-large-v3-turbo",
        "@cf/stabilityai/stable-diffusion-xl-base-1.0",
        "@cf/myshell-ai/melotts",
        "@cf/facebook/bart-large-cnn",
    ],
)
def test_cloudflare_refuses_to_force_a_chat_payload_onto_non_chat_models(model: str) -> None:
    http = Http(_ok_body())

    with pytest.raises(ValueError, match="CLOUDFLARE_MODEL_UNSUPPORTED"):
        _adapter(http, model=model)

    assert http.calls == 0


@pytest.mark.parametrize(
    ("model", "account_id", "message"),
    [
        ("model?api_key=payload-secret", ACCOUNT_ID, "MODEL_IDENTIFIER_INVALID"),
        ("../other/ai/run/x", ACCOUNT_ID, "MODEL_IDENTIFIER_INVALID"),
        ("", ACCOUNT_ID, "MODEL_IDENTIFIER_INVALID"),
        (MODEL, "other/../..", "CLOUDFLARE_ACCOUNT_ID_INVALID"),
        (MODEL, "acct?x=1", "CLOUDFLARE_ACCOUNT_ID_INVALID"),
        (MODEL, "acct id", "CLOUDFLARE_ACCOUNT_ID_INVALID"),
        (MODEL, "", "CLOUDFLARE_ACCOUNT_ID_INVALID"),
    ],
)
def test_cloudflare_rejects_unsafe_model_or_account_identifiers(
    model: str,
    account_id: str,
    message: str,
) -> None:
    http = Http(_ok_body())

    with pytest.raises(ValueError, match=message):
        CloudflareWorkersAiAdapter(http, model, API_TOKEN, account_id=account_id)

    assert http.calls == 0


def test_cloudflare_rejects_an_arbitrary_endpoint_before_a_bearer_request() -> None:
    http = Http(_ok_body())

    with pytest.raises(ValueError, match="CLOUDFLARE_ENDPOINT_INVALID"):
        CloudflareWorkersAiAdapter(
            http,
            MODEL,
            API_TOKEN,
            account_id=ACCOUNT_ID,
            endpoint="https://attacker.example/collect",
        )

    assert http.calls == 0


class Http:
    def __init__(self, body: Any = None, *, status_code: int = 200) -> None:
        self.body = _ok_body() if body is None else body
        self.status_code = status_code
        self.calls = 0
        self.last_url = ""
        self.last_json: dict[str, Any] = {}
        self.last_headers: dict[str, str] = {}
        self.last_timeout: int | None = None

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> "Response":
        self.calls += 1
        self.last_url = url
        self.last_json = json
        self.last_headers = headers
        self.last_timeout = timeout
        return Response(self.body, self.status_code)


class StatusHttp(Http):
    def __init__(self, status_code: int, body: Any = None) -> None:
        super().__init__(body, status_code=status_code)


class RaisingHttp:
    def __init__(self, exception: Exception) -> None:
        self.exception = exception
        self.calls = 0
        self.last_json: dict[str, Any] = {}

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> None:
        self.calls += 1
        self.last_json = json
        raise self.exception


class MalformedJsonHttp:
    def __init__(self) -> None:
        self.calls = 0

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> "MalformedJsonResponse":
        self.calls += 1
        return MalformedJsonResponse()


class MalformedJsonResponse:
    status_code = 200

    def json(self) -> Any:
        raise ValueError(f"secret={API_TOKEN} account={ACCOUNT_ID}")


class Response:
    def __init__(self, body: Any, status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code

    def json(self) -> Any:
        return self.body


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


def replace_source_text(request: TranslationRequest, source_text: str) -> TranslationRequest:
    return TranslationRequest(
        context=request.context,
        source_segment_id=request.source_segment_id,
        source_text=source_text,
        source_language=request.source_language,
        target_language=request.target_language,
        terms=request.terms,
        tm_list=request.tm_list,
        domain_instruction=request.domain_instruction,
        story_memory=request.story_memory,
    )
