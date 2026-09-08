# Phân tích hệ thống hiện tại

Ngày kiểm tra: **07/09/2026**. Baseline Git: **4a9598d**. Đây là nghiên cứu source, không phải chứng nhận runtime. Không sửa source, chạy migration, cài dependency, đọc giá trị secret hay gọi dịch vụ tính phí.

## 1. Phương pháp và giới hạn

- Đọc README, manifest, entry points, schema ORM, routes và các workflow/provider liên quan; đối chiếu tests và lịch sử Git. Các liên kết source dưới đây trỏ tới checkout tại thời điểm nghiên cứu.
- Không có `.codegraph/` tại root nên bỏ CodeGraph theo AGENTS.md; không tự index. `rg.exe` trong WinGet bị lỗi thực thi; dùng `git ls-files`, `Select-String` và đọc phạm vi dòng.
- TasteSkill dùng cho nghiên cứu thiết kế; Superpowers brainstorming 6.1.1 tìm được trong plugin cache của Claude. `.superpowers/sdd` trong repo là báo cáo lịch sử, không phải skill đang thực thi.
- Chưa chạy lại test suite, migration trên bản sao, benchmark dịch/TTS hay visual QA. Các kết quả test cũ không phải kết quả của commit hiện tại.
- Nghiên cứu bao phủ các ranh giới chính, không tuyên bố audit bảo mật đầy đủ từng dòng. Schema dưới đây là schema khai báo trong code; chưa kiểm tra schema/data SQLite cá nhân đang sử dụng.
- Tài liệu yêu cầu dừng tại quyết định kiến trúc lớn: bộ research này đi tới phương án để duyệt; MASTER SPEC và task plan chi tiết chưa được khóa.

## 2. Stack và deployment thực tế

| Thành phần | Hiện trạng | Bằng chứng |
|---|---|---|
| Runtime backend | Python >=3.12,<3.13 | [pyproject.toml](/D:/truyenaudio-studio/backend/pyproject.toml:5) |
| HTTP | FastAPI 0.116.1, Uvicorn 0.35.0, Pydantic 2.11.7 | Cùng manifest |
| Persistence | SQLAlchemy 2.0.43, SQLite WAL, FK bật, busy timeout 5 giây; Alembic 4 revision | [base.py](/D:/truyenaudio-studio/backend/app/db/base.py:63), `backend/migrations/versions` |
| Frontend | React 19.1.1, Router 7.8.1, TypeScript 5.8.3, Vite 7.1.2 | [package.json](/D:/truyenaudio-studio/frontend/package.json) |
| State/form | React state/effect trong route; dependencies có TanStack Query, React Hook Form, Zod; không đồng nghĩa tất cả route dùng chúng | [router.tsx](/D:/truyenaudio-studio/frontend/src/routes/router.tsx:267) |
| Styling | Phần lớn inline styles, chưa có hệ token/theme thống nhất trên màn hình chính | [router.tsx](/D:/truyenaudio-studio/frontend/src/routes/router.tsx:1109) |
| Package manager | Python pip/editable package; frontend npm/package-lock | README và manifests |
| Auth | Chủ sở hữu local, không có tài khoản/multi-tenant; CSRF token và origin check | [security.py](/D:/truyenaudio-studio/backend/app/api/security.py:9) |
| Process | API + một worker, loopback 127.0.0.1:8765; concurrency cấu hình khóa 1 | [config.py](/D:/truyenaudio-studio/backend/app/settings/config.py:7), [worker.py](/D:/truyenaudio-studio/backend/app/worker.py:179) |
| Artifact | Filesystem, hash/content metadata trong SQLite; FFmpeg master audio | `modules/artifacts`, `providers/ffmpeg_audio.py` |
| Frontend production | FastAPI serve `frontend/dist`, SPA fallback | [main.py](/D:/truyenaudio-studio/backend/app/main.py:92) |
| Khởi động | `run-studio.bat` → preflight/migrate → API/worker → readiness → browser | [run-studio.bat](/D:/truyenaudio-studio/run-studio.bat) |

`python-dotenv` được import trong `main.py`, nhưng không khai báo trực tiếp trong dependencies. Đây là nợ packaging mức thấp cần kiểm tra clean install; chưa kết luận môi trường hiện tại thiếu package.

## 3. Bản đồ module

| Module | Trách nhiệm thực | Ranh giới/coupling |
|---|---|---|
| `api/projects.py`, `modules/sources/*` | Preview/import paste, TXT, EPUB, DOCX, folder; normalize, snapshot | ProjectWorkflow cuối cùng tạo revision; không nên thay parser chỉ để đổi UI |
| `api/wenku.py`, `modules/sources/wenku.py`, scripts crawler | Có luồng Wenku trong checkout hiện tại | Tách ingress này khỏi translation core; nghiên cứu này không chạy hoặc mở rộng crawler |
| `modules/translation/workflow.py` | Chọn adapter, cache, dịch từng segment, QA, revision, approval | Một class gộp orchestration/persistence/async bridge; 766 dòng |
| `translation/segmenter.py` | Chia theo câu/ngoặc và số ký tự Hán | Không phải tokenizer đa ngôn ngữ |
| `translation/glossary.py`, `story_memory.py` | Thuật ngữ revision; đọc memory theo khoảng chương | Chưa thấy service tạo/duyệt summary tự động; workflow lặp lại truy vấn memory |
| `translation/review.py`, `repair.py` | Queue review; đề xuất sửa chọn đoạn | Repair proposals giữ trong dict process `_PROPOSALS`, mất khi restart |
| `providers/*` | Qwen/Gemini MT, reviewer, local/cloud TTS, fake | Có Protocol nhưng factory/chính sách chưa thống nhất |
| `modules/voices`, `modules/speech` | Catalog, role plan, narration, segment render/master | Có nền tảng single/multi voice; preview/render production còn khoảng trống |
| `modules/jobs/*` | Lease, heartbeat, cancel, retry, checkpoint, batch | Hạ tầng tồn tại nhưng worker launcher không wire handler thật |
| `modules/budgets`, `compliance` | Rate card, reservation, usage, consent, quyền xuất | Có thể tái sử dụng; Gemini hiện đi vòng qua guard |
| `modules/exports`, `storage`, `artifacts` | Bundle/checksum, backup/restore, cleanup preview | Giữ invariants và regression fixtures |
| `api/events.py`, diagnostics | SSE snapshot/replay, log có allowlist fields | Chưa là luồng event tiến độ append-only theo mỗi transition |
| `frontend/src/routes/router.tsx` | Navigation + import + dịch + giọng + audio + export | UI, IO, logic chọn provider, key storage và styling cùng file |
| `frontend/src/features/*` | Component editor/review/voice/settings/diagnostics | Có component chỉ nối demo/test, không được route chính sử dụng |

## 4. Luồng thực tế

1. UI tạo project, preview/import; source được normalize, hash và giữ revision. Ngôn ngữ project mặc định Trung → Việt.
2. Route dịch gọi trực tiếp `/translation/fake`, `/translation/qwen` hoặc `/translation/gemini`.
3. `enqueue_translation()` thực tế dịch đồng bộ trong request: tạo run, `flush`, vòng lặp segment, `asyncio.run(adapter.translate(...))`, chạy QA và `commit` cuối chương. Tên hàm không phản ánh queue thực. [workflow.py](/D:/truyenaudio-studio/backend/app/modules/translation/workflow.py:124)
4. Cache dựa source id/hash, provider/model/version/region, prompt/style/glossary/memory. Tuy nhiên metadata chọn trước có thể khác model fallback thực tế.
5. UI sửa từng đoạn tạo revision mới, duyệt theo expected hash. Force approval hiện dismiss toàn bộ blocker đang mở, kể cả critical. [workflow.py](/D:/truyenaudio-studio/backend/app/modules/translation/workflow.py:279)
6. Voice route tự lấy preset khả dụng đầu tiên; audio render API chạy whole chapter. Catalog preview chưa tạo job bền vững. Xem [nghiên cứu TTS](vieneu-tts-research.md).
7. Audio route chính hiển thị hash và approve; không phải AudioReview component có player. Export tạo private/public bundle qua gate.
8. Review/batch có thể enqueue Job, nhưng worker mặc định trả `*_HANDLER_NOT_CONFIGURED`. Tests có thể inject handler, vì vậy tests của runner không chứng minh launcher production xử lý được job.

Sơ đồ đầy đủ: [Current Architecture](../architecture/current-architecture.md).

## 5. Schema đã có và phần còn thiếu

Schema ORM có **26 bảng**: projects; rights_evidence; rights_grants; cloud_processing_consents; chapters; source_revisions; source_segments; glossary_entries; story_memory_entries; translation_runs; translation_segments; qa_issues; provider_profiles; voice_presets; voice_plans; voice_roles; speech_segments; artifacts; jobs; job_attempts; rate_cards; budget_authorizations; usage_ledger; exports; audit_events; event_log. Số lượng này được đếm từ 26 khai báo `__tablename__`; khi triển khai vẫn phải kiểm tra metadata/migration thay vì lấy con số thủ công làm invariant.

Project đã là container một truyện: không cần thêm cả Project và Story chỉ để khớp ví dụ prompt. TranslationRun + TranslationSegment đã thể hiện phiên bản; VoicePreset/Plan/Role và SpeechSegment/Artifact đã chứa nhiều phần của AudioChapter/AudioSegment. [models.py](/D:/truyenaudio-studio/backend/app/db/models.py:70)

Khoảng trống: catalog model/capability snapshot; immutable execution settings; provenance từng attempt/segment; summary được duyệt và invalidation theo chương; dữ liệu nhân vật/quan hệ có cấu trúc; repair proposal bền vững. Có thể mở rộng bảng hiện có hoặc thêm ít bảng có lý do nghiệp vụ; chưa quyết định migration trước Q01.

## 6. Mười vấn đề ưu tiên cao nhất

Mức độ là tác động trong ứng dụng local này, không phải điểm CVSS. `Critical` = có thể phá kiểm soát dữ liệu/chi phí hoặc định danh kết quả; `High` = chặn workflow chính hoặc gây sai/mất công việc; `Medium` = giới hạn chất lượng/khả năng mở rộng; `Low` = nợ nhỏ. Không có bằng chứng đã xảy ra rò rỉ hoặc tính tiền ngoài ý muốn.

| ID / mức | Vị trí và nguyên nhân | Impact | Recommendation / tiêu chí kiểm tra |
|---|---|---|---|
| D01 Critical | Gemini route tạo consent/budget ID giả định và adapter không guard; key ở localStorage | Chính sách gửi dữ liệu/chi phí không được cưỡng chế nhất quán; key tồn tại trong browser storage | Dùng profile/credential backend chung; chặn network nếu thiếu consent hợp lệ; kiểm tra không key trong storage/URL/log |
| D02 High | Writer keyring dùng service `truyenaudio-studio`, username `provider-profile:<id>`; Qwen reader tách ref bằng `/` | Profile tạo qua API có thể không dùng được ở Qwen | Một CredentialStore chung; integration create-profile → resolve → mocked call |
| D03 High | Worker mặc định toàn `_unconfigured_recovery_handler` | Queue có trạng thái nhưng không thực thi dịch/review/audio thật qua launcher | Đăng ký handler production có input snapshot; test đúng `build_default_worker` |
| D04 High | Dịch network trong transaction có write từ `flush` đến cuối chương; audio cũng commit cuối | Giữ writer SQLite dài; lỗi muộn rollback tiến độ đã dịch, nguy cơ gọi lặp có phí | Job bền vững, transaction ngắn theo segment, attempt ledger; crash tại từng boundary |
| D05 High | Qwen wire payload/parser khác tài liệu chính thức; VieNeu command/manifest chưa đối chiếu runtime thực | Adapter passing fake fixtures có thể thất bại provider thật | Sửa contracts theo nguồn chính thức; live smoke riêng có phép, giữ NOT_RUN trước đó |
| D06 High | Gemini fallback bên trong adapter; workflow lấy model từ profile trước adapter và không persist usage/model thực từng đoạn | Cache/provenance/budget có thể gắn nhầm model; không truy ngược chi phí | Snapshot planned/actual model, attempt và usage; policy fallback ngoài adapter |
| D07 High | Preview trả JobView không enqueue; voice/audio components không nối route chính | Người dùng không nghe/so sánh giọng trước khi render theo flow mong muốn | Preview → durable job → artifact → player → chọn preset; route E2E thật |
| D08 Medium | Context không budget token, không có writer summary; memory query trùng; TM request luôn rỗng | Thiếu consistency giữa chương, dễ overflow khi memory lớn | Context snapshot có provenance, summary approved, glossary/character và TM tách biệt |
| D09 Medium | Gemini prompt cố định Trung-Việt, Han conversion hậu kỳ; segmenter đếm Hán | Không thể coi Nhật/Hàn được hỗ trợ chỉ vì provider biết ngôn ngữ đó; có nguy cơ biến đổi text ngoài review | Chính sách ngôn ngữ rõ; QA và repair có diff, không tự đổi nghĩa để xóa cảnh báo |
| D10 Medium | SSE query toàn history rồi lọc; một event identity/job không lưu từng transition; router lớn/inline styles/component rời | Tiến độ/reconnect khó chính xác; UI khó mở rộng, tải lớn | Event append-only có sequence; paginated query và editor bounded rendering; viết lại presentation layer |

Evidence D01: [route 112–139](/D:/truyenaudio-studio/backend/app/api/translation.py:112), [factory 283–287](/D:/truyenaudio-studio/backend/app/api/translation.py:283), [browser key](/D:/truyenaudio-studio/frontend/src/routes/router.tsx:278). D02: [writer](/D:/truyenaudio-studio/backend/app/api/cloud_profiles.py:83), [reader](/D:/truyenaudio-studio/backend/app/providers/qwen_mt.py:32). D03: [worker](/D:/truyenaudio-studio/backend/app/worker.py:192). D04/D06: [workflow](/D:/truyenaudio-studio/backend/app/modules/translation/workflow.py:145), [provider selection](/D:/truyenaudio-studio/backend/app/modules/translation/workflow.py:543), [segment persist](/D:/truyenaudio-studio/backend/app/modules/translation/workflow.py:666). D05: [provider research](provider-research.md), [TTS research](vieneu-tts-research.md). D08/D09: [memory query](/D:/truyenaudio-studio/backend/app/modules/translation/workflow.py:697), [request TM](/D:/truyenaudio-studio/backend/app/modules/translation/workflow.py:415), [prompt](/D:/truyenaudio-studio/backend/app/providers/gemini_mt.py:112). D10: [events](/D:/truyenaudio-studio/backend/app/api/events.py:50).

Nợ bổ sung: repair proposals trong RAM; forced approval không ghi phân biệt chấp nhận rủi ro từng issue; API nhận/trả config provider dạng dict tự do cần schema allowlist; maxOutputTokens cố định và chưa kiểm tra đầy đủ finish reason Gemini; HTTP clients/lifecycle cần audit; model picker có nhãn chất lượng chưa benchmark.

SQLite WAL cho phép reader đồng thời với writer nhưng chỉ có một writer; việc đưa network ra ngoài write transaction là ưu tiên kỹ thuật có cơ sở. [SQLite WAL](https://www.sqlite.org/wal.html)

## 7. Keep / Refactor / Rewrite / Remove

| Module | Hành động đề xuất | Lý do |
|---|---|---|
| FastAPI, React/Vite, SQLite/SQLAlchemy/Alembic | Keep | Phù hợp app cá nhân, không có yêu cầu server nhiều người |
| Source revision, artifact hash, approval hash, export bundle | Keep + regression | Bảo vệ tính truy vết và kết quả đã duyệt |
| Import parsers | Keep + contracts | Không cần rewrite để thêm model |
| JobRunner/recovery/batch | Refactor wiring | Có lease/checkpoint, còn thiếu handler production |
| Translation/Speech workflow | Refactor có giới hạn | Tách IO network/inference và persistence, giữ nghiệp vụ |
| Provider/profile/credential/prompt | Refactor; sửa adapter theo docs | Có interface để nối, không cần framework agent nặng |
| VieNeu adapter | Rewrite phần bridge nếu contract POC xác nhận sai | Engine thật quyết định interface |
| UI route shell và các màn hình chính | Rewrite presentation | Yêu cầu redesign toàn bộ; tái sử dụng behavior đã kiểm chứng |
| Hardcoded presets/fallback trong route; key localStorage | Remove sau chuyển đổi an toàn | Hợp nhất registry, credential và policy |
| Demo/fake | Giữ cho test, tách nhãn rõ | Không thay kết quả thật bằng demo |
| Crawler | Giữ phạm vi hiện có trong nghiên cứu; cô lập adapter nhập | Quyết định tiếp tục/loại bỏ thuộc phạm vi sản phẩm, không tự xóa |

## 8. Bằng chứng cần bổ sung sau khi duyệt hướng

Chạy clean install test có kiểm soát, adapter contracts từ official fixtures, launch path E2E, migration bản sao database, actual audio probe/listen, benchmark ZH→VI và đo RAM. Chưa có phép chạy cloud/model download trong nghiên cứu này. Hoàn thiện acceptance cụ thể và tasks sau [điểm quyết định](../architecture/upgrade-decision.md).
