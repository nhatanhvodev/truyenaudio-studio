from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.contracts import OperationContext, TranslationRequest, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked, CloudCallDecision
from app.modules.execution.contracts import BillingState
from app.providers.qwen_mt import ProviderBillingUnknown, QwenMtAdapter, Secret
from app.providers.transport import ProviderTransportError


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
        terms=(("门", "cua"),),
        tm_list=(("她打开门。", "Co ay mo cua."),),
        domain_instruction="Tien hiep, giu xung ho nhat quan.",
        story_memory=("Nhan vat chinh dang bi truy duoi.",),
    )


@pytest.fixture
def http_fixture() -> HttpFixture:
    return HttpFixture(Path(__file__).parents[1] / "fixtures" / "qwen_translation.json")


def _replace_languages(
    request: TranslationRequest,
    source_language: str,
    target_language: str,
) -> TranslationRequest:
    return TranslationRequest(
        context=request.context,
        source_segment_id=request.source_segment_id,
        source_text=request.source_text,
        source_language=source_language,
        target_language=target_language,
        terms=request.terms,
        tm_list=request.tm_list,
        domain_instruction=request.domain_instruction,
        story_memory=request.story_memory,
    )


@pytest.mark.asyncio
async def test_qwen_maps_terms_tm_usage(http_fixture, translation_request) -> None:
    adapter = QwenMtAdapter(
        http_fixture.client,
        "qwen-mt-flash",
        "frankfurt",
        Secret("x"),
        cloud_guard=AllowingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    result = await adapter.translate(translation_request)

    assert result.target_text == "Co ay mo cua."
    assert result.provider == "qwen"
    assert result.model == "qwen-mt-flash"
    assert result.usage[0].unit == UsageUnit.INPUT_TOKEN.value
    assert result.usage[0].measured_units == 21
    assert result.usage[1].unit == UsageUnit.OUTPUT_TOKEN.value
    payload = http_fixture.last_json
    options = payload["parameters"]["translation_options"]
    assert options["source_lang"] == "zh"
    assert options["target_lang"] == "vi"
    assert options["terms"] == [{"source": "门", "target": "cua"}]
    assert options["tm_list"] == [{"source": "她打开门。", "target": "Co ay mo cua."}]
    assert "domains" not in options
    assert payload["parameters"]["result_format"] == "message"
    assert payload["input"]["messages"] == [{"role": "user", "content": "她打开门。"}]
    assert "system" not in json.dumps(payload)
    assert http_fixture.last_headers["Authorization"] == "Bearer x"
    assert http_fixture.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_language", "target_language", "expected_source", "expected_target"),
    [
        ("zh-CN", "vi-VN", "zh", "vi"),
        ("zh", "vi", "zh", "vi"),
        ("zh-Hans", "vi", "zh", "vi"),
        ("zh-TW", "zh", "zh_tw", "zh"),
        ("zh-Hant", "en", "zh_tw", "en"),
    ],
)
async def test_qwen_maps_app_language_tags_to_official_codes(
    http_fixture,
    translation_request,
    source_language: str,
    target_language: str,
    expected_source: str,
    expected_target: str,
) -> None:
    request = _replace_languages(translation_request, source_language, target_language)
    adapter = QwenMtAdapter(
        http_fixture.client,
        "qwen-mt-flash",
        "frankfurt",
        Secret("x"),
        cloud_guard=AllowingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    result = await adapter.translate(request)

    assert result.target_text == "Co ay mo cua."
    options = http_fixture.last_json["parameters"]["translation_options"]
    assert options["source_lang"] == expected_source
    assert options["target_lang"] == expected_target


@pytest.mark.asyncio
async def test_qwen_rejects_unsupported_language_before_http(http_fixture, translation_request) -> None:
    request = _replace_languages(translation_request, "xx-XX", "vi-VN")
    adapter = QwenMtAdapter(
        http_fixture.client,
        "qwen-mt-flash",
        "frankfurt",
        Secret("x"),
        cloud_guard=AllowingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(ValueError, match="QWEN_LANGUAGE_UNSUPPORTED:xx-XX"):
        await adapter.translate(request)

    assert http_fixture.calls == 0


def test_qwen_rejects_an_arbitrary_endpoint_before_a_bearer_request(translation_request) -> None:
    attacker = HttpFixture(Path(__file__).parents[1] / "fixtures" / "qwen_translation.json")

    with pytest.raises(ValueError, match="QWEN_ENDPOINT_INVALID"):
        QwenMtAdapter(
            attacker.client,
            "qwen-mt-flash",
            "frankfurt",
            Secret("bearer-secret"),
            endpoint="https://attacker.example/collect",
        )

    assert attacker.calls == 0


def test_qwen_rejects_unsafe_model_before_http(translation_request) -> None:
    http = HttpFixture(Path(__file__).parents[1] / "fixtures" / "qwen_translation.json")

    with pytest.raises(ValueError, match="MODEL_IDENTIFIER_INVALID"):
        QwenMtAdapter(
            http.client,
            "qwen?key=payload-secret",
            "frankfurt",
            Secret("bearer-secret"),
        )

    assert http.calls == 0


@pytest.mark.asyncio
async def test_qwen_blocks_without_cloud_guard_before_http(http_fixture, translation_request) -> None:
    adapter = QwenMtAdapter(http_fixture.client, "qwen-mt-flash", "frankfurt", Secret("x"))

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CLOUD_GUARD_REQUIRED",)
    assert http_fixture.calls == 0


@pytest.mark.asyncio
async def test_qwen_requires_cloud_consent_and_budget_authorization(http_fixture, translation_request) -> None:
    missing_budget = TranslationRequest(
        context=OperationContext(
            operation_id=translation_request.context.operation_id,
            cache_key=translation_request.context.cache_key,
            timeout_seconds=translation_request.context.timeout_seconds,
            estimated_units=translation_request.context.estimated_units,
            budget_authorization_id=None,
            cloud_consent_id=translation_request.context.cloud_consent_id,
        ),
        source_segment_id=translation_request.source_segment_id,
        source_text=translation_request.source_text,
        source_language=translation_request.source_language,
        target_language=translation_request.target_language,
        terms=translation_request.terms,
        tm_list=translation_request.tm_list,
        domain_instruction=translation_request.domain_instruction,
        story_memory=translation_request.story_memory,
    )
    adapter = QwenMtAdapter(http_fixture.client, "qwen-mt-flash", "frankfurt", Secret("x"))

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(missing_budget)

    assert "BUDGET_AUTHORIZATION_REQUIRED" in exc.value.reasons
    assert http_fixture.calls == 0


@pytest.mark.asyncio
async def test_qwen_denied_guard_blocks_before_http(http_fixture, translation_request) -> None:
    guard = DenyingGuard()
    adapter = QwenMtAdapter(
        http_fixture.client,
        "qwen-mt-flash",
        "frankfurt",
        Secret("x"),
        cloud_guard=guard,
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(CloudCallBlocked) as exc:
        await adapter.translate(translation_request)

    assert exc.value.reasons == ("CONSENT_NOT_GRANTED",)
    assert http_fixture.calls == 0


@pytest.mark.asyncio
async def test_qwen_timeout_after_send_marks_billing_unknown_without_retry(translation_request) -> None:
    http = TimeoutAfterSendHttp()
    adapter = QwenMtAdapter(
        http,
        "qwen-mt-flash",
        "frankfurt",
        Secret("secret-value"),
        cloud_guard=AllowingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(ProviderBillingUnknown):
        await adapter.translate(translation_request)

    assert http.calls == 1
    assert "她打开门" in http.last_json["input"]["messages"][0]["content"]
    assert "secret-value" not in repr(http.last_json)


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [httpx.ReadTimeout("read timed out"), httpx.ConnectError("transport unclear")])
async def test_qwen_httpx_timeout_or_transport_after_dispatch_marks_billing_unknown(
    translation_request,
    exception: Exception,
) -> None:
    http = HttpxExceptionHttp(exception)
    adapter = QwenMtAdapter(
        http,
        "qwen-mt-flash",
        "frankfurt",
        Secret("secret-value"),
        cloud_guard=AllowingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(ProviderBillingUnknown):
        await adapter.translate(translation_request)

    assert http.calls == 1


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
async def test_qwen_http_error_matrix_uses_shared_transport(
    translation_request,
    status_code: int,
    billing_state: BillingState,
    retryable: bool,
) -> None:
    http = StatusHttp(status_code)
    adapter = QwenMtAdapter(
        http,
        "qwen-mt-flash",
        "frankfurt",
        Secret("secret-value"),
        cloud_guard=AllowingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(ProviderTransportError) as exc:
        await adapter.translate(translation_request)

    assert exc.value.code == f"HTTP_{status_code}"
    assert exc.value.billing_state is billing_state
    assert exc.value.retryable is retryable
    assert "secret-value" not in str(exc.value)


@pytest.mark.asyncio
async def test_qwen_malformed_response_uses_shared_transport_error(translation_request) -> None:
    http = MalformedJsonHttp()
    adapter = QwenMtAdapter(
        http,
        "qwen-mt-flash",
        "frankfurt",
        Secret("secret-value"),
        cloud_guard=AllowingGuard(),
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(ProviderTransportError) as exc:
        await adapter.translate(translation_request)

    assert exc.value.code == "MALFORMED_RESPONSE"
    assert exc.value.billing_state is BillingState.UNKNOWN


@pytest.mark.asyncio
async def test_qwen_rejects_empty_source_before_http(http_fixture, translation_request) -> None:
    empty = TranslationRequest(
        context=translation_request.context,
        source_segment_id=translation_request.source_segment_id,
        source_text=" ",
        source_language=translation_request.source_language,
        target_language=translation_request.target_language,
        terms=translation_request.terms,
        tm_list=translation_request.tm_list,
        domain_instruction=translation_request.domain_instruction,
        story_memory=translation_request.story_memory,
    )
    adapter = QwenMtAdapter(http_fixture.client, "qwen-mt-flash", "frankfurt", Secret("x"))

    with pytest.raises(ValueError, match="QWEN_SOURCE_REQUIRED"):
        await adapter.translate(empty)

    assert http_fixture.calls == 0


class HttpFixture:
    def __init__(self, fixture_path: Path) -> None:
        self.response_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.calls = 0
        self.last_json: dict[str, Any] = {}
        self.last_headers: dict[str, str] = {}

    @property
    def client(self) -> "HttpFixture":
        return self

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int):
        self.calls += 1
        self.last_json = json
        self.last_headers = headers
        self.last_timeout = timeout
        return StubResponse(self.response_payload)


class TimeoutAfterSendHttp:
    def __init__(self) -> None:
        self.calls = 0
        self.last_json: dict[str, Any] = {}

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int):
        self.calls += 1
        self.last_json = json
        raise TimeoutError("provider timed out after request dispatch")


class HttpxExceptionHttp:
    def __init__(self, exception: Exception) -> None:
        self.exception = exception
        self.calls = 0

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int):
        self.calls += 1
        raise self.exception


class StubResponse:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class StatusHttp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls = 0

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int):
        self.calls += 1
        return StatusResponse(self.status_code)


class StatusResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return {"error": "secret=do-not-leak"}


class MalformedJsonHttp:
    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int):
        return MalformedJsonResponse()


class MalformedJsonResponse:
    status_code = 200

    def json(self) -> dict[str, Any]:
        raise ValueError("secret=do-not-leak")


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
