from __future__ import annotations

from inspect import isawaitable
from typing import Any

import httpx

from app.contracts import TranslationRequest, TranslationResult, Usage, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked
from app.modules.execution.contracts import BillingState
from app.providers.transport import ProviderTransportError, normalize_http_error


PROVIDER = "openrouter"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


class ProviderBillingUnknown(Exception):
    """The provider may have processed a request but billing cannot be confirmed."""


class OpenRouterAdapter:
    def __init__(
        self,
        http_client: object,
        model: str,
        api_key: str,
        *,
        cloud_guard: object | None = None,
        project_id: str | None = None,
        provider_profile_id: str | None = None,
        endpoint: str = ENDPOINT,
    ) -> None:
        self.http_client = http_client
        self.model = model
        self.api_key = api_key
        self.cloud_guard = cloud_guard
        self.project_id = project_id
        self.provider_profile_id = provider_profile_id
        self.endpoint = endpoint

    def capabilities(self) -> dict[str, object]:
        return {"provider": PROVIDER, "model": self.model, "api_kind": "chat", "network": True}

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        self._guard(request)
        if not request.source_text.strip():
            raise ValueError("OPENROUTER_SOURCE_REQUIRED")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Translate faithfully from the source language to the target language."},
                {"role": "user", "content": self._user_content(request)},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            response = self.http_client.post(self.endpoint, json=payload, headers=headers, timeout=request.context.timeout_seconds)
            if isawaitable(response):
                response = await response
        except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderBillingUnknown("OPENROUTER_BILLING_UNKNOWN") from exc
        status_code = getattr(response, "status_code", 200)
        if status_code != 200:
            raise normalize_http_error(status_code, "provider error", request_sent=True)
        try:
            body = response.json()
        except Exception as exc:
            raise ProviderTransportError("MALFORMED_RESPONSE", "provider response is not JSON", False, BillingState.UNKNOWN, status_code) from exc
        if not isinstance(body, dict):
            raise ProviderTransportError("MALFORMED_RESPONSE", "provider response shape is invalid", False, BillingState.UNKNOWN, status_code)
        choices = body.get("choices") or []
        content = choices[0].get("message", {}).get("content") if choices else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderTransportError("EMPTY_RESPONSE", "provider returned no translation", False, BillingState.UNKNOWN, status_code)
        usage = body.get("usage") or {}
        provider_request_id = body.get("id") if isinstance(body.get("id"), str) else None
        return TranslationResult(
            target_text=content.strip(),
            provider=PROVIDER,
            model=str(body.get("model") or self.model),
            provider_version="chat.completions",
            usage=(
                Usage(UsageUnit.INPUT_TOKEN.value, _usage(usage.get("prompt_tokens")), provider_request_id),
                Usage(UsageUnit.OUTPUT_TOKEN.value, _usage(usage.get("completion_tokens")), provider_request_id),
            ),
        )

    def _guard(self, request: TranslationRequest) -> None:
        if self.cloud_guard is None:
            raise CloudCallBlocked(("CLOUD_GUARD_REQUIRED",))
        if self.project_id is None or self.provider_profile_id is None:
            raise CloudCallBlocked(("CLOUD_GUARD_CONTEXT_REQUIRED",))
        decision = self.cloud_guard.evaluate(
            project_id=self.project_id,
            provider_profile_id=self.provider_profile_id,
            operation_id=request.context.operation_id,
            estimated_usage=(Usage(UsageUnit.INPUT_TOKEN.value, request.context.estimated_units),),
            category=request.context.billing_category,
            cloud_consent_id=request.context.cloud_consent_id,
            budget_authorization_id=request.context.budget_authorization_id,
        )
        if not decision.allowed:
            raise CloudCallBlocked(decision.reasons)

    @staticmethod
    def _user_content(request: TranslationRequest) -> str:
        glossary = "\n".join(f"{source} -> {target}" for source, target in request.terms)
        return (
            f"Source language: {request.source_language}\nTarget language: {request.target_language}\n"
            f"Glossary:\n{glossary}\n\n[{request.source_segment_id}] {request.source_text}"
        )


def _usage(value: Any) -> int:
    if type(value) is not int or value < 0:
        return 0
    return value
