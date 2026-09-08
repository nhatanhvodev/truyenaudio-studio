# Báo cáo điều hành — Nâng cấp Truyện Audio Studio

Ngày: 07/09/2026. Đây là kết quả research/spec/plan, không phải implementation report. Chưa chạy lại suite, chưa visual QA, chưa benchmark quality/TTS và chưa gọi cloud có tính phí.

## 1. Tôi đã hiểu project như thế nào

Đây là app cá nhân local-first trên Windows: FastAPI + SQLAlchemy/SQLite WAL, filesystem artifacts, một worker loopback và React/Vite. Project là container của truyện; source/translation/audio dùng revision/hash; glossary, story memory, rights, cloud consent, budget, jobs, diagnostics và export đã có. Flow nghiệp vụ tồn tại nhưng translation network còn đồng bộ trong request, worker launcher chưa nối handler production, và presentation UI đang là route inline với nhiều component chưa được gắn.

## 2. Mười vấn đề lớn nhất

D01 Gemini bypass guard và lưu key browser; D02 keyring writer/reader lệch format; D03 worker handler unavailable; D04 transaction dài bao network; D05 Qwen wire contract chưa khớp tài liệu; D06 fallback không đủ provenance/budget; D07 voice preview/player thiếu trên route thật; D08 context/TM/approved summary chưa đủ; D09 prompt/language hard-code; D10 SSE/UI scale và route coupling. Bảng impact/evidence/recommendation đầy đủ ở [current-system-analysis.md](current-system-analysis.md).

## 3. Nên giữ lại

FastAPI/React/Vite/SQLite WAL/one-worker local; source revision và artifact checksum; approval theo hash; rights/consent/budget gate; glossary/story memory nền; JobRunner lease/retry/recovery; export private/public manifest; fake provider cho test.

## 4. Nên refactor

Provider/profile/credential/prompt/context; Translation/Speech orchestration; durable job handler/attempt/usage; SSE event projection; settings/config schema. Refactor boundary và transaction, không thay domain chỉ để đẹp.

## 5. Nên rewrite

Presentation UI và route shell theo workspace chương + inspector; VieNeu bridge nếu POC contract xác nhận giả CLI; hard-coded provider/model/key UX sau khi có compatibility path. Không xóa crawler/data/demo trong phiên này.

## 6. Architecture đề xuất

Current → target: route branch → typed API/application orchestration → immutable ExecutionPlan → registry/native adapters → durable segment jobs → artifact/probe/master → review/export. Chi tiết Mermaid ở [target-architecture.md](../architecture/target-architecture.md).

## 7. Multi-provider strategy

P0 sửa credential/guard/provenance/wire contracts; Phase 1 Gemini + OpenRouter + một runtime local sau khi chọn; Phase 2 Qwen/Groq; NIM, Cerebras, Cloudflare, HF và runtime còn lại optional. OpenRouter compatibility không thay capability; Qwen native MT không ép vào chat adapter. Ma trận và nguồn chính thức ở [provider-research.md](provider-research.md).

## 8. Free-model strategy

Không hứa “free” chung: free quota phụ thuộc account/tier/region/thời điểm. Qwen-MT là candidate bulk sau contract fix; Gemini Flash candidate hiện có; OpenRouter `:free` candidate routing; Ollama/LM Studio là local/no token charge nhưng tốn RAM/điện; Cerebras trial không phải free lâu dài. Tất cả quality labels hiện `UNMEASURED`; chọn bằng corpus benchmark, không bằng marketing.

## 9. Translation Engine

Mặc định Balanced: dịch → QA xác định → review/polish đoạn rủi ro. Snapshot glossary, exact TM, approved summaries, character/addressing, style/prompt và token budget; không truncate nguồn; sửa upstream đánh dấu downstream stale. Human approval vẫn cần vì nghiên cứu dịch văn học cho thấy lỗi bỏ sót/nghiêm trọng còn tồn tại dù có document context. Xem [translation-engine-research.md](translation-engine-research.md).

## 10. VieNeu-TTS

Giữ one narrator mặc định; preview trước select; segment job resumable; master/probe/player sau đó export. POC package 3.2.9 hiện cho thấy adapter giả định CLI không được xác nhận; cần pin manifest/license, probe API Python thật, xác định capability speed/pitch/style và resample native 48 kHz về output contract 44.1 kHz. Xem [vieneu-tts-research.md](vieneu-tts-research.md).

## 11. UI/UX

Đã chọn workspace C: multi-document tabs/dock với Thư viện/Công việc/Cài đặt; trong project có Chương/Thuật ngữ/Nhân vật/Audio/Xuất; navigator chương + source/target + inspector. Mobile dùng stack/tabs/drawers; tab eviction phải bảo toàn draft; có focus/IME/reflow/loading/error/stale states; không badge quality nếu chưa benchmark; player là artifact thật. Xem [ui-ux-research.md](ui-ux-research.md).

## 12. Migration

Backup → decisions → credential/registry → durable translation/context → worker → UI workspace → TTS preview/chunks → audio/export → benchmark/cleanup. Giữ legacy route và data/revision để rollback; migration additive và idempotent. Xem [migration-plan.md](../plans/migration-plan.md).

## 13. Implementation milestones

M0 baseline/backup/contracts; M1 credential/cloud guard/provider registry; M2 durable translation/context; M3 UI rewrite; M4 VieNeu preview/segment jobs; M5 export/observability/performance. Task-level files, dependencies và acceptance ở [implementation-plan.md](../plans/implementation-plan.md).

## 14. Risks

Provider drift/quota; cost escalation hoặc billing unknown; translation inconsistency; VieNeu API/license/RAM; UI rewrite regression; SQLite WAL growth; rights/crawler boundary. Mitigation là snapshots, fail-closed gates, bounded retry, human approval, benchmark, backup và giữ artifact cũ.

## 15. Decisions đã khóa

1. Q01 = C Scalable.
2. Q02 = C Multi-document tabs/dock.
3. Q03 = C Chưa bật local runtime trong Phase 1.
4. Q04 = C Quality/Maximum opt-in.

Các lựa chọn đã được chủ sở hữu duyệt. Coding model có thể dùng master spec/plan làm baseline; các quyết định con vẫn phải ghi ADR và có benchmark/rollback.

## 16. Nghiên cứu bổ sung v2

Ba review độc lập đã bổ sung các điểm sau:

- [Security/observability v2](security-observability-research-v2.md): xếp Gemini key trong browser/query string, keyring reference lệch và Gemini guard bypass vào nhóm Critical; loopback giảm remote exposure nhưng không phải authentication.
- [Performance/reliability v2](performance-reliability-research-v2.md): chỉ ra transaction/network coupling, worker handler chưa wire, SSE load toàn history/N+1, project/chapter query và audio thiếu HTTP Range; đề xuất benchmark 1.000/10.000 chương và 500+ segments.
- [Product validation v2](product-validation-research-v2.md): xác nhận route thật chưa chứng minh end-to-end playback, đề xuất usability test T1–T7, acceptance thresholds và phân biệt rõ FACT/INFERENCE/GAP/DECISION.

Các báo cáo v2 không thay đổi source; chúng là evidence đầu vào cho baseline đã khóa.
