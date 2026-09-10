"""Cerebras Cloud chat-completions adapter (plan X03).

Wire contract per the provider research (docs/research/provider-research.md
[C1]): the Cerebras inference API exposes an OpenAI-compatible chat
completions surface at POST https://api.cerebras.ai/v1/chat/completions with
Bearer authentication. Streaming is the documented SSE form of the same
endpoint (stream: true, "data: {...}" frames terminated by "data: [DONE]").

Design rules shared with the other cloud adapters:

* the cloud guard runs before any other work, so a missing or denied guard
  never sends a request;
* a request that was already dispatched and whose outcome is ambiguous is
  reported as CEREBRAS_BILLING_UNKNOWN and is never retried here (retry policy
  lives in the J02 policy layer);
* HTTP failures are normalized through the shared transport helper, and
  malformed or empty bodies fail closed with BillingState.UNKNOWN;
* the model recorded in the result is the model the provider reports, so a
  provider-side substitution is never silently reported as the requested model.

The credential is only ever placed in the Authorization header: it is not part
of the request body, the URL, or any returned or raised value.
"""

from __future__ import annotations

from inspect import isawaitable
from typing import Any, AsyncIterator

import httpx

from app.contracts import TranslationRequest, TranslationResult, Usage, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked
from app.modules.execution.contracts import BillingState
from app.providers.transport import (
    IncrementalSseParser,
    ProviderTransportError,
    bearer_json_headers,
    normalize_http_error,
    normalize_transport_exception,
)


PROVIDER = "cerebras"
ENDPOINT = "https://api.cerebras.ai/v1/chat/completions"
# No provider version string was observed, so the result records the wire
# surface that was actually used instead of an invented vendor version.
PROVIDER_VERSION = "chat.completions"
SOURCE_REQUIRED = "CEREBRAS_SOURCE_REQUIRED"
BILLING_UNKNOWN = "CEREBRAS_BILLING_UNKNOWN"

_SYSTEM_PROMPT = "Translate faithfully from the source language to the target language."


class ProviderBillingUnknown(Exception):
    """The provider may have processed a request but billing cannot be confirmed."""


class CerebrasAdapter:
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
        # Stream state: only what the provider actually reported, never a value
        # synthesized from the request.
        self.last_stream_model = model
        self.last_stream_usage: tuple[Usage, ...] = ()

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": PROVIDER,
            "model": self.model,
            "api_kind": "chat",
            "network": True,
            "streaming": True,
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

    async def stream_translate(self, request: TranslationRequest) -> AsyncIterator[str]:
        """Yield incremental translation deltas from the Cerebras SSE stream.

        Frames are parsed with the shared incremental SSE parser, so network
        chunk boundaries may fall anywhere: mid-frame, mid-JSON, or inside a
        multi-byte UTF-8 codepoint. The last model and usage frame the provider
        sends are kept on the adapter for stream_result; nothing is synthesized
        when the provider omits usage.
        """
        self._guard(request)
        if not request.source_text.strip():
            raise ValueError(SOURCE_REQUIRED)
        payload = self._payload(request)
        payload["stream"] = True
        headers = bearer_json_headers(self.api_key, extra={"Accept": "text/event-stream"})
        parser = IncrementalSseParser()
        self.last_stream_model = self.model
        self.last_stream_usage = ()

        stream_cm = self.http_client.stream(
            "POST",
            self.endpoint,
            json=payload,
            headers=headers,
            timeout=request.context.timeout_seconds,
        )
        async with stream_cm as response:
            status_code = getattr(response, "status_code", 200)
            if status_code != 200:
                raise normalize_http_error(status_code, "provider error", request_sent=True)
            try:
                async for chunk in response.aiter_bytes():
                    for event in parser.feed(chunk):
                        data = event.get("data")
                        if not isinstance(data, dict) or data.get("done"):
                            continue
                        self._record_stream_metadata(data)
                        delta = _delta_content(data)
                        if delta:
                            yield delta
            except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
                error = normalize_transport_exception(exc, request_started=True)
                if error.billing_state is BillingState.UNKNOWN:
                    raise ProviderBillingUnknown(BILLING_UNKNOWN) from exc
                raise error from exc
        parser.finish()

    def stream_result(self, text: str) -> TranslationResult:
        """Final result for a streamed attempt.

        The accumulated streamed text is the accepted output. The model and
        usage come from the stream frames (the requested model only as the
        fallback when the provider never echoed one). An empty stream is
        malformed, never a silently empty translation.
        """
        cleaned = text.strip()
        if not cleaned:
            raise ProviderTransportError(
                "EMPTY_RESPONSE",
                "provider stream produced no translation",
                False,
                BillingState.UNKNOWN,
                None,
            )
        return TranslationResult(
            target_text=cleaned,
            provider=PROVIDER,
            model=self.last_stream_model,
            provider_version=PROVIDER_VERSION,
            usage=tuple(self.last_stream_usage),
        )

    def _record_stream_metadata(self, data: dict[str, Any]) -> None:
        reported = data.get("model")
        if isinstance(reported, str) and reported.strip():
            self.last_stream_model = reported
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return
        request_id = data.get("id") if isinstance(data.get("id"), str) else None
        self.last_stream_usage = (
            Usage(UsageUnit.INPUT_TOKEN.value, _usage(usage.get("prompt_tokens")), request_id),
            Usage(UsageUnit.OUTPUT_TOKEN.value, _usage(usage.get("completion_tokens")), request_id),
        )

    def _payload(self, request: TranslationRequest) -> dict[str, object]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": self._user_content(request)},
            ],
            "stream": False,
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


def _delta_content(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    return content if isinstance(content, str) else ""


def _reported_model(value: Any, requested: str) -> str:
    """Record the model the provider actually reports.

    A provider answering with a different model than the one requested is
    surfaced faithfully: the result carries the reported model, so a silent
    substitution can never be mistaken for the requested one.
    """
    if isinstance(value, str) and value.strip():
        return value
    return requested


def _usage(value: Any) -> int:
    if type(value) is not int or value < 0:
        return 0
    return value
