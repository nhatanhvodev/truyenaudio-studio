from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.contracts import OperationContext, QaSeverity, ReviewRequest, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked, CloudCallDecision
from app.providers.gpt_luna_reviewer import GptLunaReviewer, ProviderBillingUnknown, Secret


@pytest.fixture
def review_request() -> ReviewRequest:
    return ReviewRequest(
        context=OperationContext(
            operation_id="review-segment-001",
            cache_key="review-cache-001",
            timeout_seconds=12,
            estimated_units=55,
            budget_authorization_id="auth-001",
            cloud_consent_id="consent-001",
        ),
        source_segment_id="seg-001",
        source_text="林动打开门。",
        target_text="Lam Dong mo cua.",
    )


@pytest.mark.asyncio
async def test_luna_reviewer_uses_guard_and_structured_findings(review_request) -> None:
    guard = RecordingGuard()
    http = HttpFixture(Path(__file__).parents[1] / "fixtures" / "reviewers" / "gpt_luna_findings.json", guard)
    luna = GptLunaReviewer(
        http.client,
        Secret("secret-value"),
        cloud_guard=guard,
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    result = await luna.review(review_request)

    assert result.model == "gpt-5.6-luna"
    assert result.findings[0].source_segment_id == review_request.source_segment_id
    assert result.findings[0].severity == QaSeverity.MAJOR.value
    assert result.usage[0].unit == UsageUnit.INPUT_TOKEN.value
    assert result.usage[0].measured_units == 34
    assert result.usage[1].unit == UsageUnit.OUTPUT_TOKEN.value
    assert result.usage[1].measured_units == 12
    assert http.guard_was_checked_before_request
    assert http.calls == 1
    assert guard.estimated_units == [UsageUnit.INPUT_TOKEN.value, UsageUnit.OUTPUT_TOKEN.value]
    schema = http.last_json["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["findings"]["items"]["additionalProperties"] is False
    assert "<source_text>" in http.last_json["messages"][1]["content"]
    assert "secret-value" not in repr(http.last_json)


@pytest.mark.asyncio
async def test_luna_denied_guard_blocks_before_http(review_request) -> None:
    guard = RecordingGuard(allowed=False)
    http = HttpFixture(Path(__file__).parents[1] / "fixtures" / "reviewers" / "gpt_luna_findings.json", guard)
    luna = GptLunaReviewer(
        http.client,
        Secret("secret-value"),
        cloud_guard=guard,
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(CloudCallBlocked) as exc:
        await luna.review(review_request)

    assert exc.value.reasons == ("CONSENT_NOT_GRANTED",)
    assert http.calls == 0


@pytest.mark.asyncio
async def test_luna_timeout_after_send_marks_billing_unknown_without_retry(review_request) -> None:
    guard = RecordingGuard()
    http = TimeoutAfterSendHttp()
    luna = GptLunaReviewer(
        http,
        Secret("secret-value"),
        cloud_guard=guard,
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(ProviderBillingUnknown):
        await luna.review(review_request)

    assert http.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [httpx.ReadTimeout("read timed out"), httpx.ConnectError("transport unclear")])
async def test_luna_httpx_timeout_or_transport_after_dispatch_marks_billing_unknown(
    review_request,
    exception: Exception,
) -> None:
    guard = RecordingGuard()
    http = HttpxExceptionHttp(exception)
    luna = GptLunaReviewer(
        http,
        Secret("secret-value"),
        cloud_guard=guard,
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(ProviderBillingUnknown):
        await luna.review(review_request)

    assert http.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        "http_error",
        {"id": "bad-json", "model": "gpt-5.6-luna", "choices": [{"message": {"content": "not-json"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        {"id": "missing-choices", "model": "gpt-5.6-luna", "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        {"id": "bad-usage", "model": "gpt-5.6-luna", "choices": [{"message": {"content": "{\"findings\":[]}"}}], "usage": {"prompt_tokens": -1, "completion_tokens": 1}},
    ],
)
async def test_luna_unclear_result_after_send_marks_billing_unknown(review_request, response) -> None:
    guard = RecordingGuard()
    http = AmbiguousResultHttp(response)
    luna = GptLunaReviewer(
        http,
        Secret("secret-value"),
        cloud_guard=guard,
        project_id="project-001",
        provider_profile_id="profile-001",
    )

    with pytest.raises(ProviderBillingUnknown):
        await luna.review(review_request)

    assert http.calls == 1


class HttpFixture:
    def __init__(self, fixture_path: Path, guard: "RecordingGuard") -> None:
        self.response_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.guard = guard
        self.guard_was_checked_before_request = False
        self.calls = 0
        self.last_json: dict[str, Any] = {}

    @property
    def client(self) -> "HttpFixture":
        return self

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int):
        self.calls += 1
        self.guard_was_checked_before_request = self.guard.checked
        self.last_json = json
        return StubResponse(self.response_payload)


class TimeoutAfterSendHttp:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int):
        self.calls += 1
        raise TimeoutError("provider timed out after request dispatch")


class HttpxExceptionHttp:
    def __init__(self, exception: Exception) -> None:
        self.exception = exception
        self.calls = 0

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int):
        self.calls += 1
        raise self.exception


class AmbiguousResultHttp:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls = 0

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int):
        self.calls += 1
        return AmbiguousResponse(self.response)


class AmbiguousResponse:
    def __init__(self, response: object) -> None:
        self.response = response

    def raise_for_status(self) -> None:
        if self.response == "http_error":
            raise RuntimeError("502 Bad Gateway")

    def json(self) -> dict[str, Any]:
        if isinstance(self.response, dict):
            return self.response
        return {}


class StubResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class RecordingGuard:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.checked = False
        self.estimated_units: list[str] = []

    def evaluate(self, **kwargs: object) -> CloudCallDecision:
        self.checked = True
        self.estimated_units = [usage.unit for usage in kwargs["estimated_usage"]]
        if not self.allowed:
            return CloudCallDecision(False, None, None, (), (), ("CONSENT_NOT_GRANTED",))
        return CloudCallDecision(True, str(kwargs["cloud_consent_id"]), str(kwargs["budget_authorization_id"]), (), (), ())
