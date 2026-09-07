"""Google AI Studio (Gemini) Machine Translation Adapter for Webnovel Translation.
Connects to https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
using an API key from aistudio.google.com.
"""
from __future__ import annotations

import os
import re
from typing import Any
from inspect import isawaitable

import httpx

from app.contracts import (
    TranslationRequest,
    TranslationResult,
    Usage,
    UsageUnit,
)
from app.modules.compliance.cloud import CloudCallBlocked


DEFAULT_MODEL = "gemini-2.5-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def normalize_model_name(raw_model: str | None) -> str:
    cleaned = (raw_model or "").strip()
    if cleaned.startswith("models/"):
        cleaned = cleaned[len("models/"):]
    return cleaned or DEFAULT_MODEL


async def list_models(api_key: str, http_client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    """Fetch available models from Google AI Studio to help users pick the right one."""
    client = http_client or httpx.AsyncClient()
    url = f"{GEMINI_API_BASE}?key={api_key}"
    response = client.get(url, timeout=15)
    if isawaitable(response):
        response = await response
    if response.status_code != 200:
        return []
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
        api_key: str | None = None,
        *,
        model: str = DEFAULT_MODEL,
        http_client: Any = None,
        cloud_guard: Any = None,
        project_id: str | None = None,
        provider_profile_id: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "").strip()
        self.model = normalize_model_name(model)
        self.http_client = http_client or httpx.AsyncClient()
        self.cloud_guard = cloud_guard
        self.project_id = project_id
        self.provider_profile_id = provider_profile_id

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

        if self.cloud_guard and self.project_id and self.provider_profile_id:
            estimated_usage = (Usage(UsageUnit.CHARACTER.value, len(source_text)),)
            decision = self.cloud_guard.evaluate(
                project_id=self.project_id,
                provider_profile_id=self.provider_profile_id,
                operation_id=request.context.operation_id,
                estimated_usage=estimated_usage,
                category=request.context.billing_category,
                cloud_consent_id=request.context.cloud_consent_id,
                budget_authorization_id=request.context.budget_authorization_id,
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

        # Build list of models to try (primary + fallbacks for 404/503 errors)
        models_to_try = [self.model]
        fallbacks = ["gemini-flash-lite-latest", "gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-pro-latest"]
        for fb in fallbacks:
            if fb not in models_to_try:
                models_to_try.append(fb)

        timeout = max(30, request.context.timeout_seconds)
        last_error = None
        successful_response_data = None
        actual_model_used = self.model

        for current_model in models_to_try:
            endpoint = f"{GEMINI_API_BASE}/{current_model}:generateContent?key={self.api_key}"
            headers = {"Content-Type": "application/json"}

            try:
                response = self.http_client.post(
                    endpoint,
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                )
                if isawaitable(response):
                    response = await response
            except Exception as exc:
                last_error = f"Network error on {current_model}: {exc}"
                continue

            if response.status_code == 200:
                successful_response_data = response.json()
                actual_model_used = current_model
                break

            err_detail = ""
            try:
                err_data = response.json()
                err_detail = err_data.get("error", {}).get("message", "")
            except Exception:
                err_detail = response.text[:200]

            last_error = f"HTTP {response.status_code} ({current_model}): {err_detail}"

            # Retry next fallback model if 404 (model not available for user) or 503 (high demand)
            if response.status_code in {404, 503}:
                continue

            # Hard stop for auth / quota / bad request errors
            if response.status_code == 400:
                raise ValueError(f"GEMINI_INVALID_REQUEST: {err_detail or response.text}")
            elif response.status_code in {401, 403}:
                raise ValueError(f"GEMINI_API_KEY_INVALID: {err_detail or 'API key không hợp lệ.'}")
            elif response.status_code == 429:
                raise RuntimeError(f"GEMINI_RATE_LIMIT_EXCEEDED (429): {err_detail or 'Quá giới hạn request.'}")
            elif response.status_code >= 500 and response.status_code != 503:
                raise RuntimeError(f"GEMINI_SERVER_ERROR_{response.status_code}: {err_detail}")

        if not successful_response_data:
            available = await list_models(self.api_key, self.http_client)
            model_names = [m["name"].replace("models/", "") for m in available[:10]]
            hint = ", ".join(model_names) if model_names else "Không lấy được danh sách"
            raise ValueError(
                f"GEMINI_ALL_MODELS_FAILED: Đã thử các model ({', '.join(models_to_try)}) nhưng đều gặp lỗi. "
                f"Lỗi cuối: {last_error}. Các model khả dụng trên key của bạn: {hint}"
            )

        data = successful_response_data

        candidates = data.get("candidates") or []
        if not candidates:
            raise ValueError("GEMINI_EMPTY_RESPONSE")

        content_parts = candidates[0].get("content", {}).get("parts", [])
        if not content_parts or not content_parts[0].get("text"):
            raise ValueError("GEMINI_NO_TEXT_RETURNED")

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
        prompt_tokens = usage_meta.get("promptTokenCount", 0)
        candidates_tokens = usage_meta.get("candidatesTokenCount", 0)

        usage = (
            Usage(UsageUnit.INPUT_TOKEN.value, prompt_tokens),
            Usage(UsageUnit.OUTPUT_TOKEN.value, candidates_tokens),
        )

        return TranslationResult(
            target_text=target_text,
            provider="gemini",
            model=actual_model_used,
            provider_version="v1beta",
            usage=usage,
        )
