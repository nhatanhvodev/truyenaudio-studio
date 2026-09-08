# Testing Plan (LOCKED SCOPE; Q01–Q04 = C/C/C/C)

Bản cập nhật 08/09/2026: [plan v2](implementation-plan.md) mục 6–7 là ma trận task/test, ngưỡng Option C và release gate chính thức; [contract C01–C08](../specs/implementation-contracts.md) là nguồn fixture. Không dùng các mục tổng quan dưới đây để bỏ qua task Settings, draft streaming hoặc kiểm định live.

## Unit

State machine, segmentation boundary, glossary conflict/scope, context token budget, prompt envelope/hash, model filter, retry classifier, cache key, audio naming, checksum và redaction.

## Contract/provider

Fixture đúng wire của từng adapter; list-model pagination/status; auth/429/5xx/timeout/malformed/finish reason; usage and actual-model extraction; Qwen native vs OpenAI-compatible; credential round-trip fake keyring. Không dùng fake fixture để claim live success.

## Integration

Project import/revision → translation job → QA/repair/approval → audio invalidation; worker claim/heartbeat/cancel/recovery; consent/rights/budget fail-closed; artifact atomic write/probe/master; export private/public.

## UI/E2E

Route thật: import, configure, queue, resume, edit conflict, QA override, model filter, voice sample/custom preview, audio playback/seek/approve, export. Keyboard-only, IME, screen reader status, focus return, 320px/390px/1024px/1366px, reduced motion/dark mode.

## Performance/quality

Corpus bilingual có quyền xử lý; 30 đoạn + 3×3 chapter; score và critical gate theo translation research. Đo p50/p95 latency, tokens/cost, retry rate, memory peak, 1k/10k chapter list, 500+ audio segments, cold/warm TTS. Ghi model/prompt/context hashes và ngày.

Usability T2 của research được điều chỉnh theo Q03 C: dùng fake/no-network fixture hoặc cloud đã authorize, không yêu cầu local LLM Phase 1. Đo TTS thật riêng; fake audio không chứng minh voice quality.

## Release gates

Backend/frontend/build/E2E xanh; migration backup/restore pass; không secret trong browser persistence/response/log; request nhập key transient được phép theo C06, inference request chỉ gửi profile ID; no unverified quality badges; paid/cloud/audio live smoke chỉ chuyển từ `NOT_RUN` khi consent, budget, ledger, provider invocation và output playback đều có evidence.
