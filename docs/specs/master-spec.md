# Truyen Audio Studio — Master Spec (LOCKED BASELINE)

Ngày cập nhật 08/09/2026. Q01–Q04 đã được người dùng khóa: **C/C/C/C**. Đây là thiết kế mục tiêu, chưa phải chứng nhận implementation. [Contract bổ sung C01–C08](implementation-contracts.md) khóa chi tiết schema/API/state/UI; [plan v2](../plans/implementation-plan.md) chia milestone và task nhỏ. Research giữ vai trò bằng chứng, không ghi đè ADR.

## 1. Product Overview

Ứng dụng local-first trên Windows để nhập truyện có quyền xử lý, dịch Trung–Việt, hiệu đính, tạo audiobook VieNeu-TTS và xuất bundle để upload thủ công sang app chính.

## 2. Goals

Giữ tính truy vết revision; dịch nhất quán theo context/glossary/nhân vật; provider/model có metadata; audio có preview và resumable render; UI dễ đọc, accessible và dùng được với truyện lớn; secrets, rights, consent và budget fail-closed.

## 3. Non-goals

Không public SaaS/multi-tenant; không crawler/bypass CAPTCHA/DRM; không tự upload app chính; không tự chuyển free sang paid; không cam kết chất lượng/ngôn ngữ chưa benchmark; không bật local runtime trong Phase 1. Vector/knowledge memory được phép nghiên cứu theo Q01 C nhưng chỉ thêm sau ADR, benchmark và migration review; Redis/Celery vẫn ngoài baseline.

## 4. Current System Analysis

FastAPI + SQLAlchemy/SQLite WAL + filesystem artifacts + one worker + React/Vite. Domain đã có Project/Chapter/SourceRevision, TranslationRun/Segment, Glossary, StoryMemory, VoicePlan/Role, Speech/Artifact/Job/Budget/Rights/Export. UI route còn inline, provider branch riêng và audio preview/player chưa nối. Xem [current-system-analysis](../research/current-system-analysis.md).

## 5. Problems

D01 Gemini bypass guard/key localStorage; D02 keyring ref mismatch; D03 worker handler chưa cấu hình; D04 request giữ transaction dài; D05 Qwen wire mismatch; D06 fallback provenance/budget; D07 preview/player thiếu; D08 context/TM chưa đủ; D09 language/prompt hard-code; D10 SSE/UI scale. Full evidence ở current analysis.

## 6. User Personas

Chủ sở hữu cá nhân: xử lý khoảng 50 chương/tháng, cần kiểm soát chi phí/quyền và muốn kết quả MP3 để upload thủ công. Người hiệu đính bilingual: cần source-target, issue, glossary, context và revision conflict rõ. Người vận hành local: cần readiness, queue recovery, logs redacted và backup.

## 7. User Flows

Import/preview → cấu hình profile/model/style → quote + consent/rights preflight → enqueue → theo dõi job → review/edit/QA → approve translation → voice catalog/sample/custom preview → render segments/master → nghe/approve audio → export private/public gate.

## 8. Functional Requirements

FR-01 nhập paste/TXT/EPUB/DOCX/folder theo quyền; FR-02 snapshot/hash source; FR-03 profile/model picker có capability; FR-04 translation job resumable; FR-05 glossary/TM/character/context snapshot; FR-06 deterministic QA + review/polish opt-in; FR-07 revision approval/conflict; FR-08 voice list/sample/preview/select; FR-09 TTS segment/master/SRT; FR-10 rights/cloud/budget gate; FR-11 export checksum/provenance; FR-12 diagnostics/SSE/cancel/retry; FR-13 backup/restore.

## 9. Non-functional Requirements

Loopback mặc định; secret không vào SQLite/browser/log; approval immutable; crash-safe; transaction ngắn; output idempotent; UI keyboard/IME/reflow 320px; performance đo trên máy 8 GB; mọi claim provider/quality có nguồn hoặc `UNMEASURED`.

## 10. Translation Engine

Execution plan khóa input revision, profile/model, prompt/style, glossary, context và budget. Mặc định Balanced: translate → deterministic QA; review/polish không tự gọi model. Quality/Maximum chỉ opt-in, quote và approve từng stage theo C06. Source không bị truncate khi overflow; chunk ở ranh giới nghĩa; streaming chỉ là draft.

## 11. Provider Architecture

Registry trả descriptor/capability và factory nhận execution authorization. Shared transport chỉ dùng cho HTTP/auth/SSE/error; native Qwen/Gemini mapping nằm adapter riêng. Không để route tự khởi tạo provider. Phase 1 Gemini + OpenRouter + Qwen-MT sau contract/credential/guard tests; Phase 2 Groq/NIM/Cerebras/Cloudflare/HF theo account/terms/capability. Ollama/LM Studio chưa bật Phase 1; VieNeu local TTS vẫn trong scope.

## 12. Model Registry

Snapshot metadata có provider/model/version/region/context/output/language/stream/structured/pricing/free/license/source/date/benchmark. Curated + dynamic hybrid; stale/error không xóa snapshot tốt cuối cùng. Filter không biến null thành free/0.

## 13. Prompt System

PromptBuilder versioned: system policy → style → glossary/character/context → user instruction → source as untrusted data; output contract expected segment IDs. Native MT builder khác chat builder. Hash toàn envelope.

## 14. Context/Memory

Approved chapter summaries, entity state, aliases/relations, prior approved examples; scope theo ordinal; relevance + token budget; candidate summary cần human approval. Sửa revision làm downstream stale có phạm vi.

## 15. Glossary

Giữ GlossaryEntry revision; bổ sung forbidden/description/scope/provenance khi cần. Locked term là hard QA; conflict chặn preflight. TM là cặp source-target riêng, exact reuse có scope; fuzzy chỉ gợi ý.

## 16. Character System

Character canonical/alias/type/gender nullable/evidence; directed addressing theo chapter; không đồng nhất Character với VoiceRole. Q01 C đã chọn; schema và migration additive theo C03, task C04 của plan, không hỏi lại Q01.

## 17. VieNeu-TTS

Pinned engine/model manifest; capability probe trước UI. Không hiện speed/pitch/style nếu engine không hỗ trợ; phân biệt playback/postprocess. Native rate theo probe/manifest; master resample 44,1 kHz nếu contract app đích yêu cầu, không giả định mọi model native 48 kHz.

## 18. Audio Pipeline

Approved translation → segments → preview/synthesis jobs → immutable artifacts → probe → FFmpeg master/SRT → playback/review → export. Cache/idempotency theo revision/voice/settings; failed segment retry; giữ master cũ.

## 19. UI Architecture

Presentation rewrite trong React/Vite, tách app shell, project navigator, translation workspace, inspector, job store, audio player, settings. Domain API client không chứa business policy/provider secret.

## 20. UX Specification

Workspace C đã chọn: multi-document tabs/dock với chapter list paged, source/target aligned by stable segment ID, inspector QA/context/glossary/character và layout lưu được. Có giới hạn tab hoạt động, đóng/evict tab không mất draft, mobile chuyển thành stack/tabs/drawers. Không render toàn novel vào DOM.

## 21. Design System

Token màu light/dark, typography CJK/Vietnamese, spacing, component states, layout/dock và đủ bảy nhóm Settings được chốt tại C07 của [contract bổ sung](implementation-contracts.md). U01 hiện thực và kiểm contrast/render; U02–U10 triển khai từng luồng, không để model coding tự chọn lại IA.

## 22. Data Model

Tái sử dụng Project như Story container và domain revision/job/artifact hiện hữu. C03 của [contract bổ sung](implementation-contracts.md) quy định bảng/field/unique/FK/index/backfill cho snapshot, profile/attempt, style/TM/character/memory/draft/layout/projection/vector. Không thêm entity trùng TranslationRun/VoicePlan/Artifact.

## 23. API Contract

Method/path, request/response, status code, revision/idempotency và compatibility wrapper được quy định tại C04 của [contract bổ sung](implementation-contracts.md). Giữ `/api/cloud-profiles` hiện hữu, không tạo kho profile thứ hai. JSON lỗi có `code`, `message`, `retryable`, `correlationId`, `details` đã redact. Coding model triển khai contract này; thay đổi boundary phải cập nhật spec và fixture trước.

## 24. Error Model

Phân loại input/rights/consent/budget/auth/rate-limit/quota/context/provider-timeout/malformed/corruption/conflict. Partial output không approved. `BILLING_UNKNOWN` khi request có thể đã tới provider.

## 25. Retry/Fallback

Exponential backoff có jitter, Retry-After, deadline và attempt cap; một policy retry duy nhất. Fallback chỉ explicit allowlist, preflight lại consent/budget/capability; lưu requested/actual/reason; không free→paid tự động. Circuit breaker persist theo provider/profile/model đã chọn trong ADR-0001; threshold/cooldown/half-open và billingUnknown theo C06.

## 26. Security

CredentialStore backend keyring; profile API chỉ trả `secretConfigured`; không localStorage/URL/log. Loopback origin + CSRF; allowlist endpoint; redact diagnostics; credential rotation/revoke; rights evidence và cloud consent snapshot.

## 27. Performance

Một worker/concurrency 1 giữ nguyên đến benchmark; transaction không bao quanh network; cache SHA-256; paging/chunk; event cursor; audio range; SQLite backup API/VACUUM INTO, không copy file WAL sống. Budget RAM/latency phải đo trên máy thật.

## 28. Observability

JSONL allowlist + audit event: correlation/job/attempt/stage/provider/model/resolved model/latency/cache/units/cost/error; raw prompt/output giữ theo policy riêng. SSE sequence replayable, không query full history mỗi reconnect.

## 29. Testing

Unit domain/segmenter/QA/prompt/cache; contract adapters từng wire/error/usage; integration guard/credential/job/recovery/artifact; TTS probe/master; UI keyboard/IME/responsive; E2E route thật; fault injection 429/timeout/5xx/overflow/malformed/outage. Paid/live audio smoke `NOT_RUN` cho tới khi được cấp phép.

## 30. Migration

P0 inventory/backup + fix credential/guard/provenance; P1 registry/adapter contracts; P2 durable jobs/translation context; P3 UI workspace; P4 TTS preview/chunk; P5 audio/export; P6 benchmark/cleanup. Giữ legacy route sau feature flag cho rollback; migration từng revision, không xóa data cũ.

## 31. Risks

Provider drift, free quota bất ổn, token/cost escalation, context inconsistency, TTS RAM/license, UI rewrite regression, SQLite WAL growth, crawler/rights risk. Mitigation: snapshots, gates, bounded jobs, benchmark, backups, manual approval.

## 32. Open Questions

Q01–Q04 đã đóng ở C/C/C/C. Provider rollout, structured memory/vector tùy chọn, tab eviction và cost ceiling theo [ADR-0001](../architecture/adr/0001-locked-subdecisions.md); chi tiết triển khai theo C01–C08 và ngưỡng kiểm định ở plan v2. Account/credential, model cài tại máy, corpus có quyền và người review là đầu vào kiểm định còn cần evidence, không phải câu hỏi sản phẩm cần hỏi lại.

## 33. Acceptance Criteria

Không có cloud call nếu profile/credential/consent/rights/budget không hợp lệ; provider/model actual và usage truy vết được; job restart không nhân output; translation giữ source segments/glossary/approved context; audio preview chọn được và render segment resume; UI route thật có player/error/loading/keyboard/reflow; export bundle kiểm checksum/provenance; paid smoke chỉ báo thành công sau live evidence.

## Research bổ sung bắt buộc đọc trước implementation

`docs/research/security-observability-research-v2.md`, `performance-reliability-research-v2.md` và `product-validation-research-v2.md` là các phụ lục nghiên cứu độc lập. Chúng bổ sung threat model, tải truyện dài, usability và acceptance gap; không được hiểu là kết quả benchmark đã đạt.
