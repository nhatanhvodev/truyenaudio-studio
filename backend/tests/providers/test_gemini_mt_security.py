from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.contracts import OperationContext, TranslationRequest
from app.modules.compliance.cloud import CloudCallBlocked, CloudCallDecision
from app.modules.execution.contracts import BillingState
from app.providers.gemini_mt import GeminiMtAdapter, list_models
from app.providers.transport import ProviderTransportError


SECRET = "AIza-secret-value-that-must-not-leak"


class _CredentialStore:
    def resolve(self, profile_id: str, secret_ref: str):
        assert secret_ref.startswith("keyring:")
        return SimpleNamespace(value=SECRET)


class _Response:
    status_code = 503
    text = f"provider rejected key {SECRET}"
    headers = {"x-request-id": "request-503"}

    def json(self):
        return {"error": {"message": self.text}}


class _ProviderErrorClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def post(self, url: str, *, json, headers: dict[str, str], timeout: int):
        self.calls.append((url, headers))
        return _Response()

    async def get(self, url: str, *, headers: dict[str, str], timeout: int):
        self.calls.append((url, headers))
        return _Response()


class _UrlErrorClient(_ProviderErrorClient):
    async def post(self, url: str, *, json, headers: dict[str, str], timeout: int):
        self.calls.append((url, headers))
        request = httpx.Request("POST", f"{url}?key={SECRET}")
        raise httpx.ConnectError(f"failed request {request.url}", request=request)


def _request() -> TranslationRequest:
    return TranslationRequest(
        context=OperationContext(
            operation_id="translate-gemini-security",
            cache_key="gemini-security",
            timeout_seconds=5,
            estimated_units=1,
            budget_authorization_id="budget-1",
            cloud_consent_id="consent-1",
        ),
        source_segment_id="segment-1",
        source_text="你好",
        source_language="zh-CN",
        target_language="vi-VN",
        terms=(),
        tm_list=(),
        domain_instruction=None,
        story_memory=(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("client", [_ProviderErrorClient(), _UrlErrorClient()])
async def test_gemini_keeps_key_out_of_url_and_redacts_provider_failures(client) -> None:
    adapter = GeminiMtAdapter(
        api_key_ref="keyring:truyenaudio-studio/provider-profile:profile-1",
        credential_store=_CredentialStore(),
        http_client=client,
        cloud_guard=AllowingGuard(),
        project_id="project-1",
        provider_profile_id="profile-1",
    )

    with pytest.raises(RuntimeError) as caught:
        await adapter.translate(_request())

    assert SECRET not in str(caught.value)
    assert client.calls
    assert all("?" not in url and SECRET not in url for url, _headers in client.calls)
    assert all(headers["x-goog-api-key"] == SECRET for _url, headers in client.calls)


@pytest.mark.asyncio
async def test_gemini_requires_cloud_guard_before_http() -> None:
    client = _ProviderErrorClient()
    adapter = GeminiMtAdapter(
        api_key_ref="keyring:truyenaudio-studio/provider-profile:profile-1",
        credential_store=_CredentialStore(),
        http_client=client,
    )

    with pytest.raises(CloudCallBlocked) as caught:
        await adapter.translate(_request())

    assert caught.value.reasons == ("CLOUD_GUARD_REQUIRED",)
    assert client.calls == []


@pytest.mark.asyncio
async def test_gemini_maps_native_request_response_usage_and_actual_model() -> None:
    client = _SuccessClient()
    adapter = GeminiMtAdapter(
        api_key_ref="keyring:truyenaudio-studio/provider-profile:profile-1",
        credential_store=_CredentialStore(),
        http_client=client,
        cloud_guard=AllowingGuard(),
        project_id="project-1",
        provider_profile_id="profile-1",
        model="models/gemini-2.5-flash",
    )

    result = await adapter.translate(_request())

    assert result.target_text == "Xin chào."
    assert result.model == "models/gemini-2.5-flash-001"
    assert result.usage[0].measured_units == 11
    assert result.usage[0].provider_request_id == "request-123"
    assert client.last_json["systemInstruction"]["parts"][0]["text"]
    assert client.last_json["contents"][0]["parts"][0]["text"].endswith("你好")
    assert client.last_headers["x-goog-api-key"] == SECRET


@pytest.mark.asyncio
async def test_gemini_model_discovery_non_200_is_not_empty_success() -> None:
    client = _ProviderErrorClient()

    with pytest.raises(ProviderTransportError) as caught:
        await list_models(SECRET, client)

    assert caught.value.code == "HTTP_503"
    assert client.calls


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
    ],
)
async def test_gemini_http_error_matrix_uses_shared_transport(
    status_code: int,
    billing_state: BillingState,
    retryable: bool,
) -> None:
    client = _StatusClient(status_code)
    adapter = _guarded_adapter(client)

    with pytest.raises(ProviderTransportError) as caught:
        await adapter.translate(_request())

    assert caught.value.code == f"HTTP_{status_code}"
    assert caught.value.billing_state is billing_state
    assert caught.value.retryable is retryable
    assert SECRET not in str(caught.value)
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_gemini_timeout_after_dispatch_uses_shared_transport_without_retry() -> None:
    client = _TimeoutClient()
    adapter = _guarded_adapter(client)

    with pytest.raises(ProviderTransportError) as caught:
        await adapter.translate(_request())

    assert caught.value.code == "PROVIDER_TIMEOUT"
    assert caught.value.billing_state is BillingState.UNKNOWN
    assert caught.value.retryable is True
    assert client.calls == 1


@pytest.mark.asyncio
async def test_gemini_malformed_response_uses_shared_transport_error() -> None:
    adapter = _guarded_adapter(_MalformedClient())

    with pytest.raises(ProviderTransportError) as caught:
        await adapter.translate(_request())

    assert caught.value.code == "MALFORMED_RESPONSE"
    assert caught.value.billing_state is BillingState.UNKNOWN


class AllowingGuard:
    def evaluate(self, **kwargs):
        return CloudCallDecision(
            allowed=True,
            cloud_consent_id=kwargs["cloud_consent_id"],
            authorization_id=kwargs["budget_authorization_id"],
            rate_card_ids=("rate-1",),
            remaining_quota=(),
            reasons=(),
        )


def _guarded_adapter(client) -> GeminiMtAdapter:
    return GeminiMtAdapter(
        api_key_ref="keyring:truyenaudio-studio/provider-profile:profile-1",
        credential_store=_CredentialStore(),
        http_client=client,
        cloud_guard=AllowingGuard(),
        project_id="project-1",
        provider_profile_id="profile-1",
    )


class _SuccessResponse:
    status_code = 200
    headers = {"x-request-id": "request-123"}

    def json(self):
        return {
            "modelVersion": "models/gemini-2.5-flash-001",
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Xin chào."}]}}],
            "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7},
        }


class _SuccessClient:
    def __init__(self) -> None:
        self.calls = []
        self.last_json = {}
        self.last_headers = {}

    async def post(self, url: str, *, json, headers: dict[str, str], timeout: int):
        self.calls.append((url, headers))
        self.last_json = json
        self.last_headers = headers
        return _SuccessResponse()


class _StatusClient:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls = []

    async def post(self, url: str, *, json, headers: dict[str, str], timeout: int):
        self.calls.append((url, headers))
        return _StatusResponse(self.status_code)


class _StatusResponse:
    headers = {"x-request-id": "request-error"}

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def json(self):
        return {"error": {"message": f"provider echoed {SECRET}"}}


class _TimeoutClient:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, url: str, *, json, headers: dict[str, str], timeout: int):
        self.calls += 1
        raise TimeoutError("provider timed out after dispatch")


class _MalformedClient:
    async def post(self, url: str, *, json, headers: dict[str, str], timeout: int):
        return _MalformedResponse()


class _MalformedResponse:
    status_code = 200
    headers = {"x-request-id": "request-malformed"}

    def json(self):
        raise ValueError(f"provider echoed {SECRET}")
