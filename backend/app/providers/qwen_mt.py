from __future__ import annotations

from dataclasses import dataclass
from inspect import isawaitable
from typing import Any

import httpx

from app.contracts import TranslationRequest, TranslationResult, Usage, UsageUnit
from app.modules.compliance.cloud import CloudCallBlocked
from app.modules.security.credentials import CredentialStore, CredentialUnavailable


PROVIDER = "qwen"
PROVIDER_VERSION_FALLBACK = "unknown"
MAX_SOURCE_CHARS = 30_000


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
        endpoint: str = "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
    ) -> None:
        self.http_client = http_client
        self.model = model
        self.region = region
        self.secret = secret
        self.cloud_guard = cloud_guard
        self.project_id = project_id
        self.provider_profile_id = provider_profile_id
        self.dispatch_registry = dispatch_registry
        self.dispatch_authorization = dispatch_authorization
        self.endpoint = endpoint

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
        headers = {"Authorization": f"Bearer {self.secret.value}", "Content-Type": "application/json"}

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
            if request_started:
                raise ProviderBillingUnknown("QWEN_BILLING_UNKNOWN") from exc
            raise

        response.raise_for_status()
        body = response.json()
        translations = body.get("translations") or []
        if not translations or not str(translations[0].get("target_text", "")).strip():
            raise ValueError("QWEN_EMPTY_TRANSLATION")
        usage = body.get("usage") or {}
        provider_request_id = body.get("request_id")
        return TranslationResult(
            target_text=str(translations[0]["target_text"]),
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
        )
        if not decision.allowed:
            raise CloudCallBlocked(decision.reasons)

    def _payload(self, request: TranslationRequest) -> dict[str, object]:
        return {
            "model": self.model,
            "region": self.region,
            "source_language": request.source_language,
            "target_language": request.target_language,
            "segments": [{"segment_id": request.source_segment_id, "source_text": request.source_text}],
            "translation_options": {
                "terms": [{"source": source, "target": target} for source, target in request.terms],
                "tm_list": [{"source": source, "target": target} for source, target in request.tm_list],
                "domain": request.domain_instruction,
                "story_memory": list(request.story_memory),
            },
        }


def _non_negative_usage(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("QWEN_USAGE_INVALID")
    return value
