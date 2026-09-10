"""NVIDIA NIM chat-completions adapter (plan X02).

Wire contract per the provider research (docs/research/provider-research.md
[N1][N2]): NVIDIA NIM exposes an OpenAI-compatible chat completions surface,
both as the NVIDIA-hosted catalog and as a self-hosted NIM container.

NIM endpoints are not equivalent to each other: the hosted catalog and a
self-hosted deployment differ in host, model catalog, quota and licence
[N1][N2][N3]. The base URL is therefore *configuration*, never a fact this
adapter assumes -- a caller supplies any `https` endpoint through
`endpoint=` and the adapter keeps exactly that value. There is no discovery
call: the adapter never claims a capability it did not observe, so
`capabilities()["capability_verified"]` is False and the source of the model
name recorded for a translation is always explicit (see `last_model_source`).

Design rules shared with the other cloud adapters:

* the cloud guard runs before any other work, so a missing or denied guard
  never sends a request;
* a request that was already dispatched and whose outcome is ambiguous is
  reported as NIM_BILLING_UNKNOWN and is never retried here (retry policy lives
  in the J02 policy layer, never in the adapter);
* HTTP failures are normalized through the shared transport helper, and
  malformed or empty bodies fail closed with BillingState.UNKNOWN;
* the model recorded in the result is the model the provider reports, so a
  provider-side substitution is never silently reported as the requested model
  -- and when the provider reports none, the requested name is used *and* the
  fallback is recorded instead of being passed off as provider-observed;
* token usage is only ever what the provider returned (0 when it returned
  nothing usable), never an estimate;
* the credential is only ever placed in the Authorization header: not in the
  payload, the URL, a query parameter, or any returned/raised value.

Error codes use the short vendor prefix (`NIM_`) shared with the other
phase-2 provider adapters.
"""

from __future__ import annotations

from inspect import isawaitable
from typing import Any
from urllib.parse import urlsplit

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


PROVIDER = "nvidia-nim"
# Default for the NVIDIA-hosted catalog [N2]. A self-hosted NIM deployment
# passes its own base URL through `endpoint=`; no adapter logic treats this host
# as special beyond being the default.
ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
# The NIM chat surface returns no provider version string, so the result records
# the wire surface that was actually used instead of an invented vendor version.
PROVIDER_VERSION = "chat.completions"
SOURCE_REQUIRED = "NIM_SOURCE_REQUIRED"
BILLING_UNKNOWN = "NIM_BILLING_UNKNOWN"
ENDPOINT_UNSUPPORTED = "NIM_ENDPOINT_UNSUPPORTED"

# `last_model_source` values: where the model name in the last result came from.
# "provider" means the provider echoed it; "requested" means the provider
# reported nothing and the caller's own configuration was used as a fallback.
MODEL_SOURCE_PROVIDER = "provider"
MODEL_SOURCE_REQUESTED = "requested"

_SYSTEM_PROMPT = "Translate faithfully from the source language to the target language."


class ProviderBillingUnknown(Exception):
    """The provider may have processed a request but billing cannot be confirmed."""


class NvidiaNimAdapter:
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
        # Validated once, at construction: an endpoint this adapter cannot
        # verify never reaches the dispatch path at all.
        self.endpoint = canonical_nim_endpoint(endpoint)
        self.last_model_source = MODEL_SOURCE_REQUESTED

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": PROVIDER,
            "model": self.model,
            "api_kind": "chat",
            "network": True,
            # No discovery call is made against the configured endpoint, so the
            # capability reported here is declared by configuration and must
            # never be read as verified. Fail-closed: it stays False until a
            # separate discovery/acceptance step proves otherwise.
            "capability_verified": False,
            # Guaranteed by the endpoint validation in __init__.
            "endpoint_scheme": "https",
        }

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        # Security first: no guard decision, no bytes. Every other check (and
        # the credential) is downstream of this.
        self._guard(request)
        if not request.source_text.strip():
            raise ValueError(SOURCE_REQUIRED)
        # Reset per call so a failed or provider-silent call can never inherit a
        # stale "the provider reported this model" claim from an earlier one.
        self.last_model_source = MODEL_SOURCE_REQUESTED
        payload = self._payload(request)
        # The credential travels only in the Authorization header: an empty one
        # fails here, before anything is dispatched.
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
        model, model_source = _reported_model(body.get("model"), self.model)
        self.last_model_source = model_source
        return TranslationResult(
            target_text=content,
            provider=PROVIDER,
            model=model,
            provider_version=PROVIDER_VERSION,
            usage=(
                Usage(UsageUnit.INPUT_TOKEN.value, _usage(usage.get("prompt_tokens")), provider_request_id),
                Usage(UsageUnit.OUTPUT_TOKEN.value, _usage(usage.get("completion_tokens")), provider_request_id),
            ),
        )

    def _payload(self, request: TranslationRequest) -> dict[str, object]:
        """OpenAI-style NIM chat payload.

        `max_tokens` is optional on this surface and no per-endpoint limit is
        known to the adapter, so it is omitted: sending a cap the user never
        configured would silently truncate the translation.
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


def canonical_nim_endpoint(value: object) -> str:
    """Validate a NIM chat-completions endpoint and return it unchanged.

    NIM runs as the NVIDIA-hosted catalog and as self-hosted containers, so no
    single host is required -- but the transport must be one this adapter can
    actually verify:

    * only `https` is accepted (`http`, `file`, `ftp`, a bare host and a
      protocol-relative URL all fail closed, because sending the credential over
      an unverifiable transport is worse than refusing the configuration);
    * the host must be non-empty, so a scheme-only URL such as `https://` is
      rejected instead of being dispatched at an empty authority;
    * embedded userinfo (`https://user:pass@host/...`) is rejected: the
      credential belongs in the Authorization header, never in the URL.

    Any violation raises `ValueError(NIM_ENDPOINT_UNSUPPORTED)`.
    """
    if not isinstance(value, str):
        raise ValueError(ENDPOINT_UNSUPPORTED)
    candidate = value.strip()
    if not candidate:
        raise ValueError(ENDPOINT_UNSUPPORTED)
    parts = urlsplit(candidate)
    if parts.scheme.lower() != "https":
        raise ValueError(ENDPOINT_UNSUPPORTED)
    try:
        hostname = parts.hostname
        _ = parts.port  # an unparsable port means the authority is not usable
    except ValueError as exc:
        raise ValueError(ENDPOINT_UNSUPPORTED) from exc
    if hostname is None or not hostname.strip():
        raise ValueError(ENDPOINT_UNSUPPORTED)
    if parts.username is not None or parts.password is not None:
        raise ValueError(ENDPOINT_UNSUPPORTED)
    return candidate


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
    """First completion text, or None when the body carries no usable choice.

    A missing, empty or unusable `choices` list is an empty response: NIM never
    signals "nothing to translate" any other way, and an absent choice must
    never be read as an empty-but-successful translation.
    """
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


def _reported_model(value: Any, requested: str) -> tuple[str, str]:
    """Record the model the provider actually reports, plus where it came from.

    A provider answering with a different model than the one requested is
    surfaced faithfully: the result carries the reported model, so a silent
    substitution can never be mistaken for the requested one. When the provider
    reports nothing, the requested name is used as the fallback *and* reported
    as such through `MODEL_SOURCE_REQUESTED` -- a caller can always tell an
    observed model from a configured one.
    """
    if isinstance(value, str) and value.strip():
        return value, MODEL_SOURCE_PROVIDER
    return requested, MODEL_SOURCE_REQUESTED


def _usage(value: Any) -> int:
    if type(value) is not int or value < 0:
        return 0
    return value
