# ADR-0001 — Quyết định con cho baseline C/C/C/C

**Status:** Accepted · **Date:** 2026-09-07 · **Owner:** Local studio owner

## Context

Q01 C chọn hướng scalable; Q02 C chọn multi-document tabs/dock; Q03 C trì hoãn local runtime Phase 1; Q04 C chọn Quality/Maximum opt-in. Cần khóa các quyết định con để coding model không tự lấp khoảng trống.

## Decisions

### D1 — Provider rollout

Phase 1 gồm Gemini, OpenRouter và Qwen-MT **sau khi** contract/credential/guard tests pass. Qwen được giữ vì có use case MT chuyên dụng và đã có đường code; Gemini giữ tương thích đường hiện tại; OpenRouter kiểm chứng transport/router. Phase 2 mở Groq, NVIDIA NIM, Cerebras, Cloudflare Workers AI và Hugging Face theo account/terms/capability. Local Ollama/LM Studio không bật Phase 1.

### D2 — Routing, fallback và circuit breaker

`ProviderRegistry` tạo một `ExecutionPlan`; fallback là allowlist khai báo trong profile/policy, phải re-check capability, consent, rights và budget. Retry có một tầng với Retry-After/jitter/deadline; timeout sau gửi là `BILLING_UNKNOWN`. Circuit breaker nhẹ được persist theo provider/profile/model, cooldown và half-open probe; không tự free→paid.

### D3 — Event projection và memory

Event log append-only là nguồn audit; read projections theo project/job phục vụ SSE/list. Structured character/story graph là nguồn sự thật. Vector retrieval chỉ là index optional, local/feature-flagged, versioned theo embedding/model/license và luôn fallback về structured memory; nó không được thay locked glossary hoặc approved fact.

### D4 — Tabs/dock và eviction

Desktop cho tối đa **8 tab tài liệu hoạt động** mặc định; dock layout có thể lưu. Tab vượt giới hạn phải save draft/server revision hoặc hỏi người dùng trước khi đóng; tab inactive bị evict khỏi editor memory nhưng giữ query cache bounded. Mobile chuyển stack/tabs/drawer; không cố giữ dock desktop.

### D5 — Quality và cost ceiling

Quality/Maximum là opt-in theo chapter/batch. Mỗi stage (translate, review, polish/rewrite) cần quote, hard ceiling và approval; không bắt đầu stage kế tiếp khi chưa đủ budget. Giá unknown không coi là 0; cap mặc định kế thừa hard limit hiện tại 500.000 VND và chỉ thay bằng ADR mới.

## Consequences

Scope lớn hơn B nhưng vẫn có release gates; Phase 1 không có local fallback nên phải hiển thị rõ cloud dependency. 8-tab limit và vector feature flag giới hạn trải nghiệm power user nhưng bảo vệ RAM/consistency. Quality/Maximum tăng token/latency nên benchmark và human review là bắt buộc.

## Rejected alternatives

- Implement đủ mọi provider ngay release đầu: drift và contract surface quá lớn.
- Auto-fallback free→paid: trái cost authorization.
- Vector store làm nguồn chân lý: khó giải thích và có thể trả fact stale.
- Tabs không giới hạn: rủi ro mất draft và vượt RAM trên máy 8 GB.
- Quality rewrite mặc định mọi chapter: chi phí/latency không phù hợp và không bảo đảm tốt hơn.

## Verification

Plan v2 ngày 08/09/2026 dùng ID mới: P01–P06 kiểm provider; S03/J01–J04 guard/routing/events; U04–U07 draft/tab eviction; X06 memory index; V01–V03 benchmark; R01–R03 rollout/rollback. T00–T15 cũ có bảng ánh xạ trong plan. Chi tiết hợp đồng mục tiêu ở [implementation-contracts](../../specs/implementation-contracts.md), ngưỡng Option C ở plan. Nếu evidence mâu thuẫn, mở ADR mới, không sửa quyết định âm thầm.
