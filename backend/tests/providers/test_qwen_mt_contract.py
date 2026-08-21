from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.contracts import OperationContext, TranslationRequest, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked, CloudCallDecision
from app.providers.qwen_mt import ProviderBillingUnknown, QwenMtAdapter, Secret


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


@pytest.mark.asyncio
async def test_qwen_maps_terms_tm_usage(http_fixture, translation_request) -> None:
    adapter = QwenMtAdapter(http_fixture.client, "qwen-mt-flash", "frankfurt", Secret("x"))

    result = await adapter.translate(translation_request)

    assert result.target_text == "Co ay mo cua."
    assert result.provider == "qwen"
    assert result.model == "qwen-mt-flash"
    assert result.usage[0].unit == UsageUnit.INPUT_TOKEN.value
    assert result.usage[0].measured_units == 21
    assert result.usage[1].unit == UsageUnit.OUTPUT_TOKEN.value
    assert http_fixture.last_json["translation_options"]["terms"] == [{"source": "门", "target": "cua"}]
    assert http_fixture.last_json["translation_options"]["tm_list"] == [
        {"source": "她打开门。", "target": "Co ay mo cua."}
    ]
    assert http_fixture.last_json["translation_options"]["domain"] == "Tien hiep, giu xung ho nhat quan."
    assert http_fixture.last_headers["Authorization"] == "Bearer x"
    assert http_fixture.calls == 1


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
    adapter = QwenMtAdapter(http, "qwen-mt-flash", "frankfurt", Secret("secret-value"))

    with pytest.raises(ProviderBillingUnknown):
        await adapter.translate(translation_request)

    assert http.calls == 1
    assert "她打开门" in http.last_json["segments"][0]["source_text"]
    assert "secret-value" not in repr(http.last_json)


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


class StubResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


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
