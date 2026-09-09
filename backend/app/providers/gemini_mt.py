"""Google AI Studio (Gemini) Machine Translation Adapter for Webnovel Translation.
Connects to https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
using an API key from aistudio.google.com.
"""
from __future__ import annotations

import re
from inspect import isawaitable
from typing import Any

import httpx

from app.contracts import (
    TranslationRequest,
    TranslationResult,
    Usage,
    UsageUnit,
)
from app.modules.compliance.cloud import CloudCallBlocked
from app.modules.execution.contracts import BillingState
from app.modules.security.credentials import CredentialStore, CredentialUnavailable
from app.modules.security.model_identifier import validate_model_identifier
from app.providers.transport import (
    ProviderTransportError,
    api_key_json_headers,
    normalize_http_error,
    normalize_transport_exception,
)


DEFAULT_MODEL = "gemini-2.5-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def normalize_model_name(raw_model: str | None) -> str:
    if raw_model is None:
        return DEFAULT_MODEL
    cleaned = validate_model_identifier(raw_model)
    if cleaned.startswith("models/"):
        cleaned = cleaned[len("models/"):]
    return validate_model_identifier(cleaned)


async def list_models(api_key: str, http_client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    """Fetch available models from Google AI Studio to help users pick the right one."""
    client = http_client or httpx.AsyncClient()
    response = client.get(GEMINI_API_BASE, headers=api_key_json_headers("x-goog-api-key", api_key), timeout=15)
    if isawaitable(response):
        response = await response
    if response.status_code != 200:
        raise normalize_http_error(response.status_code, "provider error", request_sent=True)
    data = response.json()
    models = data.get("models", [])
    return [
        {
            "name": m.get("name", ""),
            "displayName": m.get("displayName", ""),
            "supportedGenerationMethods": m.get("supportedGenerationMethods", []),
        }
        for m in models
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]


class GeminiMtAdapter:
    def __init__(
        self,
        *,
        api_key_ref: str,
        credential_store: CredentialStore | None = None,
        model: str = DEFAULT_MODEL,
        http_client: Any = None,
        cloud_guard: Any = None,
        project_id: str | None = None,
        provider_profile_id: str | None = None,
        dispatch_registry: object | None = None,
        dispatch_authorization: object | None = None,
    ) -> None:
        self.model = normalize_model_name(model)
        self.api_key = self.api_key_from_ref(api_key_ref, credential_store=credential_store)
        self.http_client = http_client or httpx.AsyncClient()
        self.cloud_guard = cloud_guard
        self.project_id = project_id
        self.provider_profile_id = provider_profile_id
        self.dispatch_registry = dispatch_registry
        self.dispatch_authorization = dispatch_authorization

    @staticmethod
    def api_key_from_ref(
        secret_ref: str,
        *,
        credential_store: CredentialStore | None = None,
    ) -> str:
        if not secret_ref.startswith("keyring:"):
            raise ValueError("GEMINI_SECRET_REF_UNSUPPORTED")
        try:
            store = credential_store or CredentialStore()
            return store.resolve("", secret_ref).value
        except CredentialUnavailable:
            raise
        except ValueError as exc:
            if str(exc) == "SECRET_MISSING":
                raise ValueError("GEMINI_API_KEY_MISSING") from exc
            raise
        except Exception as exc:
            raise CredentialUnavailable("KEYRING_UNAVAILABLE") from exc

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": "gemini",
            "model": self.model,
            "region": "global",
            "source_languages": ["zh-CN", "zh", "en"],
            "target_languages": ["vi-VN", "vi"],
            "network": True,
        }

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        if self.dispatch_registry is not None or self.dispatch_authorization is not None:
            if self.dispatch_registry is None or self.dispatch_authorization is None:
                raise ValueError("PROFILE_REVISION_UNAVAILABLE")
            self.dispatch_registry.validate_authorization(self.dispatch_authorization)
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY_REQUIRED")

        source_text = request.source_text.strip()
        if not source_text:
            return TranslationResult(
                target_text="",
                provider="gemini",
                model=self.model,
                provider_version="v1beta",
                usage=(),
            )

        if not self.cloud_guard or not self.project_id or not self.provider_profile_id:
            raise CloudCallBlocked(("CLOUD_GUARD_REQUIRED",))
        estimated_usage = (Usage(UsageUnit.CHARACTER.value, request.context.estimated_units),)
        decision = self.cloud_guard.evaluate(
            project_id=self.project_id,
            provider_profile_id=self.provider_profile_id,
            operation_id=request.context.operation_id,
            estimated_usage=estimated_usage,
            category=request.context.billing_category,
            cloud_consent_id=request.context.cloud_consent_id,
            budget_authorization_id=request.context.budget_authorization_id,
            stage="TRANSLATE",
        )
        if not decision.allowed:
            raise CloudCallBlocked(decision.reasons)

        system_instruction = (
            "Bạn là dịch giả văn học và tiểu thuyết Trung - Việt hàng đầu. "
            "Nhiệm vụ: dịch văn bản tiếng Trung sang tiếng Việt mượt mà, tự nhiên, chuẩn phong cách truyện (tiên hiệp, đô thị, huyền huyễn, kiếm hiệp). "
            "Yêu cầu nghiêm ngặt:\n"
            "1. Bảo tồn 100% số liệu, số chương, ngày tháng, phần trăm, đại lượng.\n"
            "2. Sử dụng thuật ngữ và tên riêng tiếng Việt theo danh sách chỉ định nếu có.\n"
            "3. Không thêm lời mở đầu, kết thúc, chú thích dịch giả hay bọc trong markdown code block (```).\n"
            "4. Giữ nguyên định dạng xuống dòng và ngắt câu hợp lý.\n"
            "5. TUYỆT ĐỐI KHÔNG để lại bất kỳ chữ Hán / ký tự Trung Quốc nào trong bản dịch (kể cả tên riêng như 阮 -> Nguyễn, 蕊 -> Nhị). Mọi tên người, tên đất, từ Hán phải dịch hoặc phiên âm Hán-Việt 100% sang tiếng Việt."
        )

        prompt_parts: list[str] = []

        if request.terms:
            glossary_lines = [f"- {src} -> {tgt}" for src, tgt in request.terms if src and tgt]
            if glossary_lines:
                prompt_parts.append("Danh mục thuật ngữ / nhân vật khóa bắt buộc:\n" + "\n".join(glossary_lines))

        if request.domain_instruction:
            prompt_parts.append(f"Hướng dẫn phong cách:\n{request.domain_instruction}")

        if request.story_memory:
            prompt_parts.append("Bối cảnh cốt truyện gần đây:\n" + "\n".join(request.story_memory[-5:]))

        prompt_parts.append(f"Văn bản tiếng Trung cần dịch:\n{source_text}")
        user_content = "\n\n".join(prompt_parts)

        payload = {
            "systemInstruction": {
                "parts": [{"text": system_instruction}]
            },
            "contents": [
                {
                    "parts": [{"text": user_content}]
                }
            ],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 8192,
            },
        }

        timeout = max(30, request.context.timeout_seconds)
        endpoint = f"{GEMINI_API_BASE}/{self.model}:generateContent"
        headers = api_key_json_headers("x-goog-api-key", self.api_key)
        try:
            response = self.http_client.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            if isawaitable(response):
                response = await response
        except (TimeoutError, httpx.TimeoutException, httpx.TransportError, OSError) as exc:
            raise normalize_transport_exception(exc, request_started=True) from exc

        if response.status_code != 200:
            raise normalize_http_error(response.status_code, "provider error", request_sent=True)

        try:
            data = response.json()
        except Exception as exc:
            raise ProviderTransportError(
                "MALFORMED_RESPONSE",
                "provider response is not JSON",
                False,
                BillingState.UNKNOWN,
                response.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise ProviderTransportError(
                "MALFORMED_RESPONSE",
                "provider response shape is invalid",
                False,
                BillingState.UNKNOWN,
                response.status_code,
            )

        candidates = data.get("candidates") or []
        if not candidates or not isinstance(candidates[0], dict):
            raise ProviderTransportError(
                "EMPTY_RESPONSE",
                "provider returned no translation",
                False,
                BillingState.UNKNOWN,
                response.status_code,
            )

        content_parts = candidates[0].get("content", {}).get("parts", [])
        if not content_parts or not isinstance(content_parts[0], dict) or not content_parts[0].get("text"):
            raise ProviderTransportError(
                "EMPTY_RESPONSE",
                "provider returned no text",
                False,
                BillingState.UNKNOWN,
                response.status_code,
            )

        target_text = content_parts[0]["text"].strip()
        # Clean up any markdown code fencing if model accidentally returned it
        if target_text.startswith("```"):
            target_text = re.sub(r"^```[a-zA-Z]*\n?", "", target_text)
            target_text = re.sub(r"\n?```$", "", target_text).strip()

        # Clean leftover Han characters if any remain (e.g. 阮 -> Nguyễn, 蕊 -> Nhị)
        if re.search(r'[\u4e00-\u9fff]', target_text):
            from app.modules.translation.hanviet import convert_hanviet
            target_text = convert_hanviet(target_text, request.terms)

        usage_meta = data.get("usageMetadata", {})
        prompt_tokens = _non_negative_int(usage_meta.get("promptTokenCount", 0))
        candidates_tokens = _non_negative_int(usage_meta.get("candidatesTokenCount", 0))
        actual_model_used = data.get("modelVersion") if isinstance(data.get("modelVersion"), str) else self.model
        provider_request_id = _provider_request_id(response, data)

        usage = (
            Usage(UsageUnit.INPUT_TOKEN.value, prompt_tokens, provider_request_id),
            Usage(UsageUnit.OUTPUT_TOKEN.value, candidates_tokens, provider_request_id),
        )

        return TranslationResult(
            target_text=target_text,
            provider="gemini",
            model=actual_model_used,
            provider_version="v1beta",
            usage=usage,
        )


def _non_negative_int(value: object) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def _provider_request_id(response: object, data: dict[str, object]) -> str | None:
    headers = getattr(response, "headers", {})
    if hasattr(headers, "get"):
        request_id = headers.get("x-request-id") or headers.get("x-goog-request-id")
        if isinstance(request_id, str):
            return request_id
    response_id = data.get("responseId")
    return response_id if isinstance(response_id, str) else None
