"""Cloudflare Workers AI translation adapter (account-scoped, X04).

Wire contract used here (Workers AI REST API):

    POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}
    Authorization: Bearer <api_token>

The account and the model are both *path* parameters, so the credential never
travels in a URL or query string and the account is never hard-coded: the
adapter builds the endpoint from the account_id and model it is given.
Responses are wrapped in an envelope {"result": ..., "success": bool,
"errors": [...]}: the adapter reads "result" and treats success=False as a
normalized provider failure instead of a successful translation.

Model family note: Workers AI hosts several modalities under the same
/ai/run/{model} path. Only text-generation (chat) models take a
{"messages": [{"role": "user", "content": ...}]} body, so the adapter fails
closed for known non-text-generation families (embeddings, rerankers, speech,
image, summarization) instead of forcing a chat payload onto them.

Evidence boundary: this adapter is written against the documented request and
envelope shapes; it has NOT been exercised against a live Cloudflare account.
Account plan, region, data-processing terms and the response shape of every
model family remain unverified.
"""

from __future__ import annotations

import re
from inspect import isawaitable
from typing import Any

import httpx

from app.contracts import TranslationRequest, TranslationResult, Usage, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked
from app.modules.execution.contracts import BillingState
from app.modules.security.model_identifier import MODEL_IDENTIFIER_INVALID
from app.providers.transport import ProviderTransportError, normalize_http_error


PROVIDER = "cloudflare-workers-ai"
ENDPOINT = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
PROVIDER_VERSION = "v4"
API_KIND = "chat"

ACCOUNT_ID_INVALID = "CLOUDFLARE_ACCOUNT_ID_INVALID"
ENDPOINT_INVALID = "CLOUDFLARE_ENDPOINT_INVALID"
MODEL_UNSUPPORTED = "CLOUDFLARE_MODEL_UNSUPPORTED"
SOURCE_REQUIRED = "CLOUDFLARE_SOURCE_REQUIRED"
BILLING_UNKNOWN = "CLOUDFLARE_BILLING_UNKNOWN"
API_REPORTED_FAILURE = "PROVIDER_REPORTED_FAILURE"

_ACCOUNT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}", re.ASCII)
_MODEL_IDENTIFIER = re.compile(
    r"@?[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*",
    re.ASCII,
)
_MAX_MODEL_IDENTIFIER_LENGTH = 255

# Conservative, fail-closed denylist of Workers AI families whose documented
# request body is not a chat "messages" body. Unknown families keep the chat
# payload: text-generation models are the translation target of this adapter.
NON_CHAT_MODEL_PREFIXES: tuple[str, ...] = (
    "@cf/baai/bge-",  # text embeddings and rerankers
    "@cf/baai/bge-reranker",
    "@cf/openai/whisper",  # speech recognition
    "@cf/deepgram/",  # speech recognition and text-to-speech
    "@cf/myshell-ai/melotts",  # text-to-speech
    "@cf/stabilityai/",  # image generation
    "@cf/black-forest-labs/",  # image generation
    "@cf/leonardo/",  # image generation
    "@cf/bytedance/stable-diffusion",  # image generation
    "@cf/runwayml/",  # video generation
    "@cf/facebook/bart-",  # summarization (input_text body)
    "@cf/huggingface/distilbert-",  # text classification (text body)
)

SYSTEM_PROMPT = "Translate faithfully from the source language to the target language."


class ProviderBillingUnknown(Exception):
    """The provider may have processed a request but billing cannot be confirmed."""


class CloudflareWorkersAiAdapter:
    def __init__(
        self,
        http_client: object,
        model: str,
        api_key: str,
        *,
        account_id: str,
        cloud_guard: object | None = None,
        project_id: str | None = None,
        provider_profile_id: str | None = None,
        endpoint: str = ENDPOINT,
    ) -> None:
        self.account_id = validate_account_id(account_id)
        self.model = validate_model_identifier(model)
        self.api_key = api_key
        self.http_client = http_client
        self.cloud_guard = cloud_guard
        self.project_id = project_id
        self.provider_profile_id = provider_profile_id
        self.endpoint = canonical_endpoint(endpoint, self.account_id, self.model)

    def __repr__(self) -> str:
        # The account id is account-scoped configuration and the API token is a
        # secret: neither may reach logs, traces or exception dumps.
        return f"{type(self).__name__}(model={self.model!r}, endpoint=<redacted>)"

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": PROVIDER,
            "model": self.model,
            "api_kind": API_KIND,
            "network": True,
        }

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        self._guard(request)
        if not request.source_text.strip():
            raise ValueError(SOURCE_REQUIRED)
        payload = self._payload(request)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
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
            # The request was dispatched: the call may be billable and no retry
            # or fallback happens here (retry belongs to the J02 policy layer).
            raise ProviderBillingUnknown(BILLING_UNKNOWN) from exc
        status_code = getattr(response, "status_code", 200)
        if status_code != 200:
            raise normalize_http_error(status_code, "provider error", request_sent=True)
        try:
            body = response.json()
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
        success = body.get("success")
        if not isinstance(success, bool):
            raise ProviderTransportError(
                "MALFORMED_RESPONSE",
                "provider response shape is invalid",
                False,
                BillingState.UNKNOWN,
                status_code,
            )
        if not success:
            # The envelope reported a failure. Provider error bodies are never
            # echoed: they can quote the account and the submitted text.
            raise ProviderTransportError(
                API_REPORTED_FAILURE,
                "provider reported the request failed",
                False,
                BillingState.UNKNOWN,
                status_code,
            )
        result = body.get("result")
        if not isinstance(result, dict):
            raise ProviderTransportError(
                "MALFORMED_RESPONSE",
                "provider response shape is invalid",
                False,
                BillingState.UNKNOWN,
                status_code,
            )
        content = _result_text(result)
        if content is None:
            raise ProviderTransportError(
                "EMPTY_RESPONSE",
                "provider returned no translation",
                False,
                BillingState.UNKNOWN,
                status_code,
            )
        usage = result.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        return TranslationResult(
            target_text=content.strip(),
            provider=PROVIDER,
            model=_actual_model(result, self.model),
            provider_version=PROVIDER_VERSION,
            usage=(
                Usage(UsageUnit.INPUT_TOKEN.value, _first_usage(usage, ("prompt_tokens", "input_tokens"))),
                Usage(UsageUnit.OUTPUT_TOKEN.value, _first_usage(usage, ("completion_tokens", "output_tokens"))),
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
        # Workers AI takes the model from the run path, so the body stays a pure
        # chat body with no credential and no account field.
        return {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._user_content(request)},
            ]
        }

    @staticmethod
    def _user_content(request: TranslationRequest) -> str:
        glossary = "\n".join(f"{source} -> {target}" for source, target in request.terms)
        return (
            f"Source language: {request.source_language}\nTarget language: {request.target_language}\n"
            f"Glossary:\n{glossary}\n\n[{request.source_segment_id}] {request.source_text}"
        )


def validate_account_id(value: object) -> str:
    """Return a URL-path-safe Cloudflare account identifier.

    The account id is a single path segment: separators, escapes, traversal and
    whitespace are rejected so a caller cannot rewrite the request target.
    """
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or _ACCOUNT_ID.fullmatch(value) is None
    ):
        raise ValueError(ACCOUNT_ID_INVALID)
    return value


def validate_model_identifier(value: object) -> str:
    """Return a URL-path-safe Workers AI model id such as @cf/meta/....

    Workers AI model ids may carry a leading "@" and slash-delimited
    namespaces, but never URL delimiters, escapes or dot-path traversal.
    """
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_MODEL_IDENTIFIER_LENGTH
        or _MODEL_IDENTIFIER.fullmatch(value) is None
    ):
        raise ValueError(MODEL_IDENTIFIER_INVALID)
    if _is_non_chat_model(value):
        # A chat body sent to an embeddings/speech/image model is a wrong
        # request, not a translation: fail closed before any HTTP call.
        raise ValueError(MODEL_UNSUPPORTED)
    return value


def _is_non_chat_model(model: str) -> bool:
    return model.startswith(NON_CHAT_MODEL_PREFIXES)


def build_endpoint(account_id: str, model: str) -> str:
    return ENDPOINT.replace("{account_id}", account_id).replace("{model}", model)


def canonical_endpoint(endpoint: object, account_id: str, model: str) -> str:
    """Pin the request target to the Workers AI path of this account and model.

    An arbitrary endpoint would send the bearer token to a host of the caller's
    choosing, so only the canonical account-scoped URL is accepted.
    """
    canonical = build_endpoint(account_id, model)
    if not isinstance(endpoint, str):
        raise ValueError(ENDPOINT_INVALID)
    candidate = endpoint.replace("{account_id}", account_id).replace("{model}", model)
    if candidate != canonical:
        raise ValueError(ENDPOINT_INVALID)
    return canonical


def _result_text(result: dict[str, Any]) -> str | None:
    """Read the translated text from either documented "result" shape."""
    response = result.get("response")
    if isinstance(response, str) and response.strip():
        return response
    choices = result.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content.strip():
            return content
    return None


def _actual_model(result: dict[str, Any], requested: str) -> str:
    reported = result.get("model")
    if isinstance(reported, str) and reported.strip():
        return reported
    return requested


def _first_usage(usage: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = usage.get(key)
        if type(value) is int and value >= 0:
            return value
    # No token counts reported: report zero rather than inventing a number.
    return 0
