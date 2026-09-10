"""Hugging Face hosted Inference API text-generation adapter (plan X05).

Wire contract used here (Hugging Face Inference API, serverless hosted route):

    POST https://api-inference.huggingface.co/models/{model}
    Authorization: Bearer <hf_token>

The repository id is a *path* parameter, so the credential never travels in a
URL or a query string, and the request target is pinned to the canonical host:
an arbitrary endpoint would send the bearer token to a host of the caller's
choosing. Only the text-generation task takes the documented
{"inputs": ..., "parameters": {"return_full_text": false}} body, so a different
task fails closed (HUGGINGFACE_TASK_UNSUPPORTED) instead of posting a
text-generation body to a model that cannot answer it.

Handled response shapes: [{"generated_text": ...}] and {"generated_text": ...}.
An {"error": ...} envelope, or a body without generated_text, is reported as
EMPTY_RESPONSE and never turned into invented text.

HOSTED ONLY, and no model download: this adapter makes HTTP calls and nothing
else. It never imports transformers/torch/huggingface_hub, never calls
from_pretrained/snapshot_download, and never writes a model file, so a local LLM
runtime cannot be enabled through this path (plan X05 note: local LLM stays off
in Phase 1).

Evidence boundary: written against the documented request and response shapes;
it has NOT been exercised against a live Hugging Face account. Token scope,
account plan, region, data-processing terms, gated-model acceptance, per-model
licence, and inference-provider routing/price remain unverified.
"""

from __future__ import annotations

from collections.abc import Mapping
from inspect import isawaitable
from typing import Any

import httpx

from app.contracts import TranslationRequest, TranslationResult, Usage, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked
from app.modules.execution.contracts import BillingState
from app.modules.security.model_identifier import validate_model_identifier
from app.providers.transport import (
    ProviderTransportError,
    bearer_json_headers,
    normalize_http_error,
)


PROVIDER = "huggingface"
ENDPOINT = "https://api-inference.huggingface.co/models/{model}"
# The provider reports no version string on this surface, so the result records
# the wire surface that was actually used instead of an invented vendor version.
PROVIDER_VERSION = "inference-api"
TASK_TEXT_GENERATION = "text-generation"
API_KIND = TASK_TEXT_GENERATION
# Every capability statement below describes a remote call. Nothing in this
# module loads, downloads or caches weights, so "HOSTED" is explicit rather
# than implied: a local deployment is a different product surface.
SOURCE_HOSTED = "HOSTED"

SOURCE_REQUIRED = "HUGGINGFACE_SOURCE_REQUIRED"
BILLING_UNKNOWN = "HUGGINGFACE_BILLING_UNKNOWN"
TASK_UNSUPPORTED = "HUGGINGFACE_TASK_UNSUPPORTED"
ENDPOINT_INVALID = "HUGGINGFACE_ENDPOINT_INVALID"

SUPPORTED_TASKS: frozenset[str] = frozenset({TASK_TEXT_GENERATION})

# Model metadata (a catalog snapshot) carries the pipeline/task of a repository.
# Only these keys are read, and only to decide whether this adapter may call it.
_METADATA_TASK_KEYS: tuple[str, ...] = ("task", "pipeline_tag", "pipelineTag")

_SYSTEM_INSTRUCTION = "Translate faithfully from the source language to the target language."


class ProviderBillingUnknown(Exception):
    """The provider may have processed a request but billing cannot be confirmed."""


class HuggingFaceAdapter:
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
        task: str = TASK_TEXT_GENERATION,
    ) -> None:
        self.model = validate_model_identifier(model)
        self.task = require_supported_task(task)
        self.http_client = http_client
        self.api_key = api_key
        self.cloud_guard = cloud_guard
        self.project_id = project_id
        self.provider_profile_id = provider_profile_id
        self.endpoint = canonical_endpoint(endpoint, self.model)

    def __repr__(self) -> str:
        # The token is a secret and never appears in a representation: only the
        # routing facts a log line legitimately needs are shown.
        return (
            f"{type(self).__name__}(provider={PROVIDER!r}, model={self.model!r}, "
            f"task={self.task!r}, source={SOURCE_HOSTED!r}, endpoint=<redacted>)"
        )

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": PROVIDER,
            "model": self.model,
            "api_kind": API_KIND,
            "task": self.task,
            "source": SOURCE_HOSTED,
            "local_model_download": False,
            "network": True,
        }

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        self._guard(request)
        if not request.source_text.strip():
            raise ValueError(SOURCE_REQUIRED)
        payload = self._payload(request)
        headers = bearer_json_headers(self.api_key)
        try:
            response = self.http_client.post(
                self.endpoint,
                json=payload,
                headers=headers,
                timeout=request.context.timeout_seconds,
            )
            if isawaitable(response):
                response = await response
        except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
            # The request was dispatched: it may be billable and the outcome is
            # unknown, so it is reported as such and never retried here (retry
            # and fallback belong to the J02 policy layer).
            raise ProviderBillingUnknown(BILLING_UNKNOWN) from exc
        status_code = getattr(response, "status_code", 200)
        if status_code != 200:
            raise normalize_http_error(status_code, "provider error", request_sent=True)
        body = _json_body(response, status_code)
        item = _response_item(body)
        content = _generated_text(item)
        if content is None:
            raise ProviderTransportError(
                "EMPTY_RESPONSE",
                "provider returned no translation",
                False,
                BillingState.UNKNOWN,
                status_code,
            )
        usage = _usage_object(item)
        provider_request_id = _request_id(item)
        return TranslationResult(
            target_text=content,
            provider=PROVIDER,
            model=_reported_model(item, self.model),
            provider_version=PROVIDER_VERSION,
            usage=(
                Usage(
                    UsageUnit.INPUT_TOKEN.value,
                    _first_usage(usage, ("input_tokens", "prompt_tokens")),
                    provider_request_id,
                ),
                Usage(
                    UsageUnit.OUTPUT_TOKEN.value,
                    _first_usage(usage, ("output_tokens", "completion_tokens")),
                    provider_request_id,
                ),
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
            stage="TRANSLATE",
        )
        if not decision.allowed:
            raise CloudCallBlocked(decision.reasons)

    def _payload(self, request: TranslationRequest) -> dict[str, object]:
        # Native text-generation body: the repository id travels in the request
        # path, so the body carries the prompt and the documented generation
        # parameters only. wait_for_model stays false so a cold model answers
        # with a loading error instead of holding the request open; that error is
        # normalized (and deferred to the policy layer) rather than waited on.
        return {
            "inputs": self._prompt(request),
            "parameters": {"return_full_text": False},
            "options": {"wait_for_model": False},
        }

    @staticmethod
    def _prompt(request: TranslationRequest) -> str:
        glossary = "\n".join(f"{source} -> {target}" for source, target in request.terms)
        return (
            f"{_SYSTEM_INSTRUCTION}\n"
            f"Source language: {request.source_language}\n"
            f"Target language: {request.target_language}\n"
            f"Glossary:\n{glossary}\n\n"
            f"[{request.source_segment_id}] {request.source_text}"
        )


def require_supported_task(task: object) -> str:
    """Return the task when this adapter can serve it, else fail closed.

    A text-generation body sent to an embeddings, image or speech model is a
    wrong request rather than a translation, so an unsupported task is refused
    before any HTTP call.
    """
    if not isinstance(task, str) or task not in SUPPORTED_TASKS:
        raise ValueError(TASK_UNSUPPORTED)
    return task


def resolve_task(metadata: Mapping[str, object] | None) -> str:
    """Resolve the inference task from model metadata (pipeline tag).

    Inference routing and the model task come from the catalog metadata, never
    from a guess: a snapshot without a task keeps the text-generation default,
    and any other task is refused instead of being coerced into this adapter.
    """
    if not metadata:
        return TASK_TEXT_GENERATION
    for key in _METADATA_TASK_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return require_supported_task(value.strip())
    return TASK_TEXT_GENERATION


def build_endpoint(model: str) -> str:
    return ENDPOINT.replace("{model}", model)


def canonical_endpoint(endpoint: object, model: str) -> str:
    """Pin the request target to the canonical hosted route of this model.

    An arbitrary endpoint would send the bearer token to a host of the caller's
    choosing, so only the inference URL of the configured repository is
    accepted. The model identifier is validated separately, which keeps the path
    free of delimiters, escapes and traversal.
    """
    canonical = build_endpoint(model)
    if not isinstance(endpoint, str):
        raise ValueError(ENDPOINT_INVALID)
    if endpoint.replace("{model}", model) != canonical:
        raise ValueError(ENDPOINT_INVALID)
    return canonical


def _json_body(response: object, status_code: int) -> Any:
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
    if not isinstance(body, (dict, list)):
        raise ProviderTransportError(
            "MALFORMED_RESPONSE",
            "provider response shape is invalid",
            False,
            BillingState.UNKNOWN,
            status_code,
        )
    return body


def _response_item(body: Any) -> dict[str, Any] | None:
    """Return the object carrying generated_text for either documented shape."""
    if isinstance(body, list):
        if not body:
            return None
        first = body[0]
        return first if isinstance(first, dict) else None
    if isinstance(body, dict):
        return body
    return None


def _generated_text(item: dict[str, Any] | None) -> str | None:
    """Read the translation, or None when the provider produced no text.

    An error envelope is authoritative even when it also carries a text field,
    and a missing/blank generated_text is never padded with invented content.
    """
    if item is None:
        return None
    if item.get("error") is not None:
        return None
    value = item.get("generated_text")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _usage_object(item: dict[str, Any] | None) -> dict[str, Any]:
    usage = item.get("usage") if item is not None else None
    return usage if isinstance(usage, dict) else {}


def _request_id(item: dict[str, Any] | None) -> str | None:
    value = item.get("request_id") if item is not None else None
    if isinstance(value, str) and value.strip():
        return value
    return None


def _reported_model(item: dict[str, Any] | None, requested: str) -> str:
    """Record the model the provider actually reports.

    A provider answering with a different repository than the requested one is
    surfaced faithfully, so a silent substitution can never be mistaken for the
    requested model.
    """
    value = item.get("model") if item is not None else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return requested


def _first_usage(usage: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = usage.get(key)
        if type(value) is int and value >= 0:
            return value
    # No token counts reported: report zero rather than inventing a number.
    return 0
