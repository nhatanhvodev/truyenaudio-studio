from __future__ import annotations

from typing import Any

import pytest

from app.contracts import OperationContext, TranslationRequest, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked, CloudCallDecision
from app.providers.openrouter import OpenRouterAdapter


@pytest.fixture
def translation_request() -> TranslationRequest:
    return TranslationRequest(
        context=OperationContext("op-1", "cache-1", 10, 20, "auth-1", "consent-1"),
        source_segment_id="seg-1",
        source_text="她打开门。",
        source_language="zh-CN",
        target_language="vi-VN",
        terms=(("门", "cửa"),),
        tm_list=(),
        domain_instruction="natural",
        story_memory=(),
    )


class Http:
    def __init__(self, body: dict[str, Any] | None = None) -> None:
        self.body = body or {
            "id": "req-1",
            "model": "downstream/model-v2",
            "choices": [{"message": {"content": "Cô ấy mở cửa."}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }
        self.calls = 0
        self.last_json: dict[str, Any] = {}

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: int):
        self.calls += 1
        self.last_json = json
        return Response(self.body)


class Response:
    status_code = 200

    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    def json(self) -> dict[str, Any]:
        return self.body


class Guard:
    def evaluate(self, **kwargs: object) -> CloudCallDecision:
        return CloudCallDecision(True, "consent-1", "auth-1", (), (), ())


@pytest.mark.asyncio
async def test_openrouter_maps_chat_payload_and_actual_model(translation_request: TranslationRequest) -> None:
    http = Http()
    result = await OpenRouterAdapter(http, "requested/model", "key", cloud_guard=Guard(), project_id="p", provider_profile_id="profile").translate(translation_request)
    assert result.target_text == "Cô ấy mở cửa."
    assert result.model == "downstream/model-v2"
    assert result.usage[0].unit == UsageUnit.INPUT_TOKEN.value
    assert http.last_json["model"] == "requested/model"
    assert http.last_json["messages"][-1]["content"].find("她打开门") >= 0


@pytest.mark.asyncio
async def test_openrouter_guard_blocks_before_http(translation_request: TranslationRequest) -> None:
    http = Http()
    with pytest.raises(CloudCallBlocked):
        await OpenRouterAdapter(http, "model", "key").translate(translation_request)
    assert http.calls == 0
