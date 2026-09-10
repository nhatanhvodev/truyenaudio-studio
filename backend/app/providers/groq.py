"""Groq Cloud chat-completions adapter (plan X01).

Wire contract per the provider research (docs/research/provider-research.md
[R1]-[R4]): Groq exposes an OpenAI-compatible chat completions surface at
POST https://api.groq.com/openai/v1/chat/completions with Bearer
authentication. The research records that the surface is OpenAI-style but *not*
field-for-field compatible ("co mot so field khong ho tro" [R1]), so this
adapter sends only the two fields the task pins down (model + messages) instead
of guessing at optional OpenAI parameters, and it makes no pricing claim: the
free plan in the research is per model and per account ([R4]) and has not been
verified here (see capabilities()).

Design rules shared with the other cloud adapters:

* the cloud guard runs before any other work, so a missing or denied guard
  never sends a request;
* a request that was already dispatched and whose outcome is ambiguous is
  reported as GROQ_BILLING_UNKNOWN and is never retried here (retry policy
  lives in the J02 policy layer, never in the adapter);
* HTTP failures are normalized through the shared transport helper, and
  malformed or empty bodies fail closed with BillingState.UNKNOWN;
* the model recorded in the result is the model the provider reports, so a
  provider-side substitution is never silently reported as the requested model;
* token usage is copied from the provider response or reported as 0 - a number
  the provider never sent is never invented.

The credential is only ever placed in the Authorization header: it is not part
of the request body, the URL or query string, and it appears in no returned or
raised value.
"""

from __future__ import annotations

from inspect import isawaitable
from typing import Any

import httpx

from app.contracts import TranslationRequest, TranslationResult, Usage, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked
from app.modules.execution.contracts import BillingState
from app.providers.transport import (
    ProviderTransportError,
    bearer_json_headers,
    normalize_http_error,
    normalize_transport_exception,
)


PROVIDER = "groq"
ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
# The documented response shape carries no vendor version string, so the result
# records the wire surface that was actually used instead of an invented
# provider version.
PROVIDER_VERSION = "chat.completions"
SOURCE_REQUIRED = "GROQ_SOURCE_REQUIRED"
BILLING_UNKNOWN = "GROQ_BILLING_UNKNOWN"

_SYSTEM_PROMPT = "Translate faithfully from the source language to the target language."


class ProviderBillingUnknown(Exception):
    """The provider may have processed a request but billing cannot be confirmed."""


class GroqAdapter:
    """Translate one segment through the Groq chat completions API."""

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
        # Observation only: the provider's own finish_reason for the last
        # response, or None when the provider did not report one. The adapter
        # never synthesizes it and never acts on it - completion policy belongs
        # to the execution layer.
        self.last_finish_reason: str | None = None

    def __repr__(self) -> str:
        return (
            f"GroqAdapter(model={self.model!r}, endpoint={self.endpoint!r}, "
            "credential=<redacted>)"
        )

    def capabilities(self) -> dict[str, object]:
        """Describe only what this adapter can actually serve.

        The chat surface is declared, and nothing else: no pricing, quota or
        "free" claim is made (the research free plan is per model, per account
        and unverified here), and live_verified stays False until the adapter
        has been exercised against an authorized account with the real terms.
        """
        return {
            "provider": PROVIDER,
            "model": self.model,
            "api_kind": "chat",
            "network": True,
            "live_verified": False,
            "billing": "account-dependent",
        }

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        self._guard(request)
        if not request.source_text.strip():
            raise ValueError(SOURCE_REQUIRED)
        payload = self._payload(request)
        headers = bearer_json_headers(self.api_key)

        request_started = False
        try:
            request_started = True
            response = self.http_client.post(
                self.endpoint,
                json=payload,
                headers=headers,
                timeout=request.context.timeout_seconds,
            )
            if isawaitable(response):
                response = await response
        except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
            error = normalize_transport_exception(exc, request_started=request_started)
            if error.billing_state is BillingState.UNKNOWN:
                raise ProviderBillingUnknown(BILLING_UNKNOWN) from exc
            raise error from exc

        status_code = getattr(response, "status_code", 200)
        if status_code != 200:
            raise normalize_http_error(status_code, "provider error", request_sent=True)
        body = _json_object(response, status_code)
        content = _choice_content(body)
        if content is None:
            raise ProviderTransportError(
                "EMPTY_RESPONSE",
                "provider returned no translation",
                False,
                BillingState.UNKNOWN,
                status_code,
            )
        usage = body.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        provider_request_id = body.get("id") if isinstance(body.get("id"), str) else None
        self.last_finish_reason = _finish_reason(body)
        return TranslationResult(
            target_text=content,
            provider=PROVIDER,
            model=_reported_model(body.get("model"), self.model),
            provider_version=PROVIDER_VERSION,
            usage=(
                Usage(UsageUnit.INPUT_TOKEN.value, _usage(usage.get("prompt_tokens")), provider_request_id),
                Usage(UsageUnit.OUTPUT_TOKEN.value, _usage(usage.get("completion_tokens")), provider_request_id),
            ),
        )

    def _payload(self, request: TranslationRequest) -> dict[str, object]:
        """The minimal documented OpenAI-compatible body.

        Groq does not accept every OpenAI field [R1], so no optional field is
        sent before it is verified against the provider documentation and an
        authorized account.
        """
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": self._user_content(request)},
            ],
        }

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
            stage="TRANSLATE",
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


def _json_object(response: object, status_code: int) -> dict[str, Any]:
    try:
        body = response.json()  # type: ignore[attr-defined]
    except Exception as exc:
        raise ProviderTransportError(
            "MALFORMED_RESPONSE",
            "provider response is not JSON",
            False,
            BillingState.UNKNOWN,
            status_code,
        ) from exc
    if not isinstance(body, dict):
        raise ProviderTransportError(
            "MALFORMED_RESPONSE",
            "provider response shape is invalid",
            False,
            BillingState.UNKNOWN,
            status_code,
        )
    return body


def _choice_content(body: dict[str, Any]) -> str | None:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    return content.strip()


def _finish_reason(body: dict[str, Any]) -> str | None:
    """The provider's own finish_reason, or None when it reported none."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    value = choices[0].get("finish_reason")
    if isinstance(value, str) and value:
        return value
    return None


def _reported_model(value: Any, requested: str) -> str:
    """Record the model the provider actually reports.

    A provider answering with a different model than the requested one is
    surfaced faithfully: the result carries the reported model, so a silent
    substitution can never be mistaken for the requested model.
    """
    if isinstance(value, str) and value.strip():
        return value
    return requested


def _usage(value: Any) -> int:
    if type(value) is not int or value < 0:
        return 0
    return value
