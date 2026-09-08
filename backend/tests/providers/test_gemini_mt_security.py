from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.contracts import OperationContext, TranslationRequest
from app.providers.gemini_mt import GeminiMtAdapter


SECRET = "AIza-secret-value-that-must-not-leak"


class _CredentialStore:
    def resolve(self, profile_id: str, secret_ref: str):
        assert secret_ref.startswith("keyring:")
        return SimpleNamespace(value=SECRET)


class _Response:
    status_code = 503
    text = f"provider rejected key {SECRET}"

    def json(self):
        return {"error": {"message": self.text}}


class _ProviderErrorClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def post(self, url: str, *, json, headers: dict[str, str], timeout: int):
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
    )

    with pytest.raises(ValueError) as caught:
        await adapter.translate(_request())

    assert str(caught.value) == "GEMINI_ALL_MODELS_FAILED"
    assert SECRET not in str(caught.value)
    assert client.calls
    assert all("?" not in url and SECRET not in url for url, _headers in client.calls)
    assert all(headers["x-goog-api-key"] == SECRET for _url, headers in client.calls)
