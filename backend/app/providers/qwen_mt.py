from __future__ import annotations

from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, AsyncIterator

import httpx

from app.contracts import TranslationRequest, TranslationResult, Usage, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked
from app.modules.execution.contracts import BillingState
from app.modules.security.credentials import CredentialStore, CredentialUnavailable
from app.modules.security.model_identifier import validate_model_identifier
from app.providers.transport import (
    IncrementalSseParser,
    ProviderTransportError,
    bearer_json_headers,
    normalize_http_error,
    normalize_transport_exception,
)


PROVIDER = "qwen"
PROVIDER_VERSION_FALLBACK = "unknown"
MAX_SOURCE_CHARS = 30_000
QWEN_ENDPOINT = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation"

# DashScope Qwen-MT language codes per the official docs (2026-09-09): the wire
# accepts official codes such as "zh"/"vi"/"zh_tw"/"en", not BCP-47 app tags.
# The adapter maps the app-level tags it is given and fails closed on anything
# it cannot map instead of silently sending a wrong language code.
_QWEN_LANGUAGE_CODES: dict[str, str] = {
    "zh": "zh",
    "zh-CN": "zh",
    "zh-Hans": "zh",
    "zh-Hant": "zh_tw",
    "zh-TW": "zh_tw",
    "vi": "vi",
    "vi-VN": "vi",
    "en": "en",
    "en-US": "en",
    "en-GB": "en",
    "ja": "ja",
    "ko": "ko",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "ru": "ru",
    "th": "th",
    "id": "id",
    "ms": "ms",
    "pt": "pt",
    "it": "it",
    "ar": "ar",
}


@dataclass(frozen=True)
class Secret:
    value: str

    def __repr__(self) -> str:
        return "Secret(<redacted>)"

    @classmethod
    def from_ref(
        cls,
        secret_ref: str,
        *,
        credential_store: CredentialStore | None = None,
    ) -> "Secret":
        if secret_ref.startswith("keyring:"):
            try:
                store = credential_store or CredentialStore()
                return cls(store.resolve("", secret_ref).value)
            except CredentialUnavailable:
                raise
            except ValueError as exc:
                if str(exc) == "SECRET_MISSING":
                    raise ValueError("QWEN_SECRET_MISSING") from exc
                raise
            except Exception as exc:
                raise CredentialUnavailable("KEYRING_UNAVAILABLE") from exc
        raise ValueError("QWEN_SECRET_REF_UNSUPPORTED")


class ProviderBillingUnknown(Exception):
    """Raised when a request may have reached the provider but billing is unknown."""


class QwenMtAdapter:
    def __init__(
        self,
        http_client: object,
        model: str,
        region: str,
        secret: Secret,
        *,
        cloud_guard: object | None = None,
        project_id: str | None = None,
        provider_profile_id: str | None = None,
        dispatch_registry: object | None = None,
        dispatch_authorization: object | None = None,
        endpoint: str = QWEN_ENDPOINT,
    ) -> None:
        self.http_client = http_client
        self.model = validate_model_identifier(model)
        self.region = region
        self.secret = secret
        self.cloud_guard = cloud_guard
        self.project_id = project_id
        self.provider_profile_id = provider_profile_id
        self.dispatch_registry = dispatch_registry
        self.dispatch_authorization = dispatch_authorization
        self.endpoint = canonical_qwen_endpoint(endpoint)
        self.last_stream_usage: tuple[Usage, ...] = ()

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": PROVIDER,
            "model": self.model,
            "region": self.region,
            "source_languages": ["zh-CN"],
            "target_languages": ["vi-VN"],
            "network": True,
        }

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        if self.dispatch_registry is not None or self.dispatch_authorization is not None:
            if self.dispatch_registry is None or self.dispatch_authorization is None:
                raise ValueError("PROFILE_REVISION_UNAVAILABLE")
            self.dispatch_registry.validate_authorization(self.dispatch_authorization)
        self._validate_request(request)
        self._evaluate_cloud_guard(request)
        payload = self._payload(request)
        headers = bearer_json_headers(self.secret.value)

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
                raise ProviderBillingUnknown("QWEN_BILLING_UNKNOWN") from exc
            raise error from exc

        if response.status_code != 200:
            raise normalize_http_error(response.status_code, "provider error", request_sent=True)
        try:
            body = response.json()
        except (TypeError, ValueError) as exc:
            raise ProviderTransportError(
                "MALFORMED_RESPONSE",
                "provider response is not JSON",
                False,
                BillingState.UNKNOWN,
                response.status_code,
            ) from exc
        if not isinstance(body, dict):
            raise ProviderTransportError(
                "MALFORMED_RESPONSE",
                "provider response shape is invalid",
                False,
                BillingState.UNKNOWN,
                response.status_code,
            )
        output = body.get("output")
        choices = output.get("choices") if isinstance(output, dict) else None
        if not choices or not isinstance(choices[0], dict):
            raise ProviderTransportError(
                "EMPTY_RESPONSE",
                "provider returned no translation",
                False,
                BillingState.UNKNOWN,
                response.status_code,
            )
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderTransportError(
                "EMPTY_RESPONSE",
                "provider returned no translation",
                False,
                BillingState.UNKNOWN,
                response.status_code,
            )
        usage = body.get("usage")
        if not isinstance(usage, dict):
            usage = {}
        provider_request_id = body.get("request_id")
        if provider_request_id is not None and not isinstance(provider_request_id, str):
            provider_request_id = None
        return TranslationResult(
            target_text=content.strip(),
            provider=PROVIDER,
            model=str(body.get("model") or self.model),
            provider_version=str(body.get("provider_version") or PROVIDER_VERSION_FALLBACK),
            usage=(
                Usage(UsageUnit.INPUT_TOKEN.value, _non_negative_usage(usage.get("input_tokens")), provider_request_id),
                Usage(UsageUnit.OUTPUT_TOKEN.value, _non_negative_usage(usage.get("output_tokens")), provider_request_id),
            ),
        )

    def _validate_request(self, request: TranslationRequest) -> None:
        if request.context.cloud_consent_id is None:
            raise CloudCallBlocked(("CLOUD_CONSENT_REQUIRED",))
        if request.context.budget_authorization_id is None:
            raise CloudCallBlocked(("BUDGET_AUTHORIZATION_REQUIRED",))
        if not request.source_text.strip():
            raise ValueError("QWEN_SOURCE_REQUIRED")
        if len(request.source_text) > MAX_SOURCE_CHARS:
            raise ValueError("QWEN_SOURCE_TOO_LARGE")

    def _evaluate_cloud_guard(self, request: TranslationRequest) -> None:
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

    async def stream_translate(self, request: TranslationRequest) -> AsyncIterator[str]:
        """Incremental draft stream (qwen-mt-flash/lite per provider docs).

        Uses the documented DashScope SSE mode: ``parameters.incremental_output``
        plus the ``X-DashScope-SSE: enable`` header, parsed with the shared
        incremental SSE parser. Yields only newly produced text; the final
        usage is stored on ``self.last_stream_usage``. Non-incremental models
        (plus/turbo) return the full text so far, which is de-duplicated into
        deltas. Guard/validation happen before any bytes are sent.
        """
        if self.dispatch_registry is not None or self.dispatch_authorization is not None:
            if self.dispatch_registry is None or self.dispatch_authorization is None:
                raise ValueError("PROFILE_REVISION_UNAVAILABLE")
            self.dispatch_registry.validate_authorization(self.dispatch_authorization)
        self._validate_request(request)
        self._evaluate_cloud_guard(request)
        payload = self._payload(request)
        parameters = payload.get("parameters")
        if isinstance(parameters, dict):
            parameters["incremental_output"] = True
        headers = bearer_json_headers(self.secret.value)
        headers["X-DashScope-SSE"] = "enable"
        headers["Accept"] = "text/event-stream"

        parser = IncrementalSseParser()
        accumulated = ""
        mode: str | None = None  # None until a second frame reveals the model mode
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
                        if not isinstance(data, dict):
                            continue
                        if data.get("done"):
                            continue
                        usage = data.get("usage")
                        if isinstance(usage, dict):
                            self.last_stream_usage = (
                                Usage(
                                    UsageUnit.INPUT_TOKEN.value,
                                    _non_negative_usage(usage.get("input_tokens")),
                                    data.get("request_id"),
                                ),
                                Usage(
                                    UsageUnit.OUTPUT_TOKEN.value,
                                    _non_negative_usage(usage.get("output_tokens")),
                                    data.get("request_id"),
                                ),
                            )
                        output = data.get("output")
                        choices = output.get("choices") if isinstance(output, dict) else None
                        if not choices or not isinstance(choices[0], dict):
                            continue
                        message = choices[0].get("message")
                        content = message.get("content") if isinstance(message, dict) else None
                        if not isinstance(content, str) or content == "":
                            continue
                        delta, mode = _stream_delta(accumulated, content, mode)
                        if delta:
                            accumulated += delta
                            yield delta
            except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
                error = normalize_transport_exception(exc, request_started=True)
                if error.billing_state is BillingState.UNKNOWN:
                    raise ProviderBillingUnknown("QWEN_BILLING_UNKNOWN") from exc
                raise error from exc
        parser.finish()

    def _payload(self, request: TranslationRequest) -> dict[str, object]:
        translation_options: dict[str, object] = {
            "source_lang": qwen_language_code(request.source_language),
            "target_lang": qwen_language_code(request.target_language),
        }
        if request.terms:
            translation_options["terms"] = [
                {"source": source, "target": target} for source, target in request.terms
            ]
        if request.tm_list:
            translation_options["tm_list"] = [
                {"source": source, "target": target} for source, target in request.tm_list
            ]
        return {
            "model": self.model,
            "input": {"messages": [{"role": "user", "content": request.source_text}]},
            "parameters": {
                "result_format": "message",
                "translation_options": translation_options,
            },
        }


def _non_negative_usage(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("QWEN_USAGE_INVALID")
    return value


def _stream_delta(accumulated: str, content: str, mode: str | None) -> tuple[str, str]:
    """Delta extraction for both DashScope streaming modes.

    ``incremental_output`` models (flash/lite) send only new text; plus/turbo
    send the full translation so far. The mode is decided from the second
    frame (a cumulative frame starts with the previous total) and a regression
    inside cumulative mode is reported as a malformed stream.
    """
    if mode is None or mode == "unknown":
        if not accumulated:
            return content, "unknown"
        if content == accumulated:
            return "", "unknown"
        if content.startswith(accumulated):
            return content[len(accumulated) :], "cumulative"
        return content, "incremental"
    if mode == "cumulative":
        if content == accumulated:
            return "", "cumulative"
        if not content.startswith(accumulated):
            raise ProviderTransportError(
                "MALFORMED_STREAM",
                "stream text regressed",
                False,
                BillingState.UNKNOWN,
            )
        return content[len(accumulated) :], "cumulative"
    if content == accumulated:
        return "", mode
    return content, mode


def qwen_language_code(language: str) -> str:
    """Map an app language tag to the official DashScope Qwen-MT language code.

    Fails closed on tags the adapter cannot map instead of silently sending a
    language code the provider does not understand.
    """
    code = _QWEN_LANGUAGE_CODES.get(language)
    if code is None:
        raise ValueError(f"QWEN_LANGUAGE_UNSUPPORTED:{language}")
    return code


def canonical_qwen_endpoint(value: object) -> str:
    if value != QWEN_ENDPOINT:
        raise ValueError("QWEN_ENDPOINT_INVALID")
    return QWEN_ENDPOINT
