# Provider Spec (LOCKED BASELINE; Q01 C)

## Boundary

`ProviderRegistry` resolve một `ExecutionPlan` trước job. Adapter không biết UI; route không khởi tạo adapter; cloud guard/budget/credential được inject từ backend.

## Descriptor

Provider/model snapshot phải có API kind, endpoint allowlist, auth kind, region, context/output, languages, stream, structured/native translation, usage/pricing/free policy, license/source/date. Capability thiếu là `unknown`.

## Phases

P0 sửa guard, keyring, error/attempt ledger và Qwen/Gemini contracts. Phase 1 Gemini + OpenRouter + Qwen-MT sau contract/credential/guard tests. Phase 2 Groq/NIM/Cerebras/Cloudflare/HF theo account/terms/capability; chưa bật Ollama/LM Studio/local LLM Phase 1. Không tự chuyển free thành paid. VieNeu local TTS không thuộc quyết định trì hoãn local LLM.

## Contract

`listModels`, `healthCheck`, `validateCredential`, `translate`, `stream`, `estimateCost` có chữ ký/input/output tại [contract bổ sung C02](implementation-contracts.md). Mỗi dispatch lưu requested/actual model, provider request id, usage confidence, retry/fallback và policy snapshot. Native API mapping riêng; shared HTTP/SSE transport chỉ là implementation detail. API profile giữ `/api/cloud-profiles` theo C04; secrets và quote/retry/breaker theo C06.
