# Phase 1 — Tổng hợp trạng thái bàn giao

Ngày: 2026-09-10 · Nhánh `codex/implement-upgrade-plan` · Commit tham chiếu `9a48050`

### Phiên bản đã dùng cho mọi số liệu trong tài liệu này

| Thành phần | Phiên bản |
|---|---|
| Hệ điều hành | Windows 11 (10.0.26200), AMD64 |
| Python | 3.12.13 (CPython) |
| Node.js | v24.11.1 |
| RAM máy | 7,4 GB (khả dụng ~2,2 GB lúc đo) |
| App | `truyenaudio-studio-backend` 0.1.0 (`backend/pyproject.toml`) |
| Trình duyệt E2E | Chrome hệ thống (Playwright 1.54.2, `channel: 'chrome'`) |
| FFmpeg | **không chạy được** — symlink WinGet gãy |

Yêu cầu tối thiểu: Python 3.12, Node 24.x (xem `README.md`).

## Cách đọc bảng này — ba mức trạng thái KHÁC NHAU

| Mức | Nghĩa | Điều kiện tối thiểu |
|---|---|---|
| **code complete** | Đã viết mã và có test tự động xanh | Test đơn vị/hợp đồng chạy được trên máy |
| **fixture verified** | Đã kiểm bằng fixture/route/browser THẬT trên máy này | Có lệnh + exit code + số liệu đo được. Ô bằng chứng dưới đây của nhiều dòng chỉ nêu **tên bằng chứng**; dòng nào không có lệnh riêng thì được phủ bởi lệnh suite ở mục 2 chứ không phải bằng chứng độc lập |
| **live verified** | Đã chạy với provider/model/corpus THẬT | Có credential/consent/budget hoặc model + license, và output thật |

> **Live verified: KHÔNG có mục nào trong Phase 1.**
> Máy này không có credential cloud, không có consent/budget, không có model VieNeu + license,
> không có corpus được cấp quyền, không có người review/người nghe, và không có FFmpeg chạy được
> (symlink WinGet gãy). Mọi tính năng phụ thuộc các yếu tố đó **đang bị tắt** và được ghi NOT_RUN/BLOCKED.
> Không có dòng nào dưới đây được gán nhãn live verified.

## 1. Ma trận task → trạng thái

| Task | code complete | fixture verified | live verified | Bằng chứng chính |
|---|---|---|---|---|
| F01 Baseline/backup/restore | ✅ | ✅ | — | `docs/validation/baseline.md` |
| F02 Typed contracts | ✅ | ✅ | — | contract round-trip/hash |
| F03 Schema provenance/migration | ✅ | ✅ | — | `pytest backend/tests/db` |
| S01 Keyring/credential lifecycle | ✅ | ✅ | ❌ | fake-keyring round-trip, legacy ref |
| S02 Chặn rò key/CSRF/loopback | ✅ | ✅ | — | 127+ security test |
| S03 Quote/reservation guard | ✅ | ✅ | ❌ | budget/compliance matrix |
| P01 Registry/descriptor | ✅ | ✅ | — | registry/capability matrix |
| P02 Discovery/catalog phân trang | ✅ | ✅ | ❌ | pagination/notModified/stale fixtures |
| P03 Transport/SSE parser | ✅ | ✅ | — | 400/401/403/404/429/5xx fixture |
| P04 Gemini native | ✅ | ✅ | ❌ | contract fixture; **chưa gọi cloud thật** |
| P05 Qwen-MT native | ✅ | ✅ | ❌ | contract fixture; **chưa gọi cloud thật** |
| P06 OpenRouter | ✅ | ✅ | ❌ | routing/usage fixture |
| C01 Prompt/style builder | ✅ | ✅ | — | prompt snapshot/injection |
| C02 Glossary scope/locked | ✅ | ✅ | — | conflict/scope test |
| C03 Translation memory exact | ✅ | ✅ | — | exact/mismatch, isolation |
| C04 Characters/addressing | ✅ | ✅ | — | alias ambiguity, directed relations |
| C05 Summary + context engine | ✅ | ✅ | — | budget boundaries, chapter consistency |
| C06 QA/edit/approve/repair | ✅ | ✅ | ❌ | QA fixture; repair cloud **NOT_RUN** |
| J01 Worker/handler/checkpoint | ✅ *(acceptance còn nợ)* | ✅ | ❌ | 500-segment crash/resume thật; REVIEW/SUMMARIZE chỉ nhánh **fake**. **REPAIR_TRANSLATION handler còn thiếu** — xem `progress-upgrade-plan.md` (PARTIAL) |
| J02 Retry/cancel/breaker | ✅ | ✅ | ❌ | fault matrix; cancel p95 **75,5 ms** |
| J03 Projection/feed/diagnostics | ✅ | ✅ | — | replay parity, redaction |
| J04 Draft streaming | ✅ | ✅ | ❌ | delta→draft; **delta sống giữa job NOT_RUN** |
| U01 Design system/tokens | ✅ | ✅ | — | browser audit 320/390/1024/1366, WCAG 1.4.4 |
| U02 App shell/Settings 7 nhóm | ✅ | ✅ | — | route test + browser |
| U03 Library paging + import review | ✅ | ✅ | — | browser E2E `import-preview.spec.ts` |
| U04 Draft API + optimistic | ✅ | ✅ | — | CAS 409, editor conflict |
| U05 Tabs/dock/layout | ✅ | ✅ | — | browser E2E `workspace-tabs.spec.ts` |
| U06 Editor song ngữ/QA/inspector | ✅ *(acceptance còn nợ)* | ✅ | ❌ | repair proposal preview/apply **fake**; **virtualize chưa làm** — xem `progress-upgrade-plan.md` (PARTIAL) |
| U07 Job UI + draft stream | ✅ | ✅ | ❌ | batch partial-failure + **route thật `POST /api/jobs/{id}/cancel|retry`** (wrapper `request_cancel`/`retry_failed`, mã lỗi có tên, chống gửi lại BILLING_UNKNOWN, idempotent) và UI đã nối; còn **E2E SSE trên browser NOT_RUN** — xem `progress-upgrade-plan.md` |
| U08 Provider/model settings | ✅ | ✅ | ❌ | E2E credential rotate/mask; quote ceiling cần budget live |
| U09 Style/glossary/character/memory | ✅ | ✅ | — | 4 manager + scope preview; nested project route **đã có thật** (`ProjectSettingsRoutes.tsx`, mount `router.tsx:118`, 3 test `ProjectNav.test.tsx`) — còn E2E downstream-stale NOT_RUN |
| U10 Storage/Appearance/Advanced | ✅ | ✅ | — | retention/restore-copy, flag fail-closed |
| A01 VieNeu manifest/bridge | ✅ | ✅ | ❌ | catalog/probe; **model chưa cài** |
| A02 Preview job/chọn giọng | ✅ | ✅ | ❌ | 22 backend + 14 UI test; nghe thật **NOT_RUN** |
| A03 Render segment resumable | ✅ | ✅ | ❌ | 500-segment crash/resume: calls 500, READY 500, 0 duplicate |
| A04 Part-master/final/SRT | ✅ | ✅ | ❌ | 120⇒3 part, SRT timeline thật; **FFmpeg thật NOT_RUN** |
| A05 Artifact Range/review | ✅ | ✅ | ❌ | browser E2E Range 206/416/304; nghe thật **NOT_RUN** |
| E01 Export bundle/metadata | ✅ | ✅ | ❌ | browser E2E 2 spec; upload thật là thủ công (C08) |
| V01 Fixture/benchmark | ✅ | ✅ | — | 13 fixture + 14 report JSON |
| V02 Integration/a11y/perf | ✅ | ✅ | ❌ | 14 report G-PERF, 32/34 dòng ngưỡng PASS · 0 FAIL · **2 dòng NOT_RUN** (không tính là đạt) |
| V03 Đánh giá dịch/TTS thật | — | ❌ | ❌ | `docs/validation/quality.md` — **NOT_RUN/BLOCKED toàn bộ** |
| R01 Migration/rollout theo flag | ✅ | ✅ | — | `release-gate.md`: 6 diễn tập thật |
| R02 Tài liệu vận hành/bàn giao | ✅ | ✅ | — | tài liệu này + `docs/operations/` |
| R03 Dọn đường cũ | — | ❌ | ❌ | **NOT_STARTED** — cần một chu kỳ dùng thật ổn định + usage scan |
| X01–X07 Phase 2 | — | ❌ | ❌ | **NOT_STARTED** — Phase 2 chỉ bắt đầu sau R02; không chặn Phase 1 |

## 2. Kiểm chứng đã chạy (số thật, lệnh thật)

| Lệnh | Kết quả |
|---|---|
| `.venv\Scripts\python.exe -m pytest backend/tests -q` | **917 passed**, 1 warning (FK-cycle có sẵn), 647 s |
| `cd frontend; npm test -- --run` | **267 passed / 45 file** |
| `cd frontend; npm run build` | PASS (tsc -b + vite build) |
| `cd frontend; npx --no-install playwright test` | **13 passed / 9 spec** trên Chrome hệ thống |
| `pytest backend/tests/scripts -q` | 21 passed (harness benchmark) |
| `pytest backend/tests/security -q` | 70 passed |
| `pytest backend/tests/db backend/tests/storage backend/tests/settings -q` | 83 passed (cổng R01) |
| `scripts/benchmarks/run.py --fixture <13 tên>` | **14/14 report có `overallStatus=PASS`**, nhưng 34 dòng ngưỡng = **32 PASS · 0 FAIL · 2 NOT_RUN** |

Chi tiết từng fixture (p50/p95/p99, SQL, RSS, WAL) nằm trong `docs/validation/perf-*.json`.

## 3. KNOWN LIMITS — điều Phase 1 KHÔNG chứng minh được

### 3.1 Chưa từng chạy với môi trường thật

| Hạn chế | Vì sao | Feature đang tắt |
|---|---|---|
| Chất lượng dịch thật | Không có credential/consent/budget cloud | `cloud_quality_mode` |
| Chất lượng giọng VieNeu | Không có model + license trên máy | `vieneu_tts_live` |
| Master audio thật | FFmpeg không chạy được (symlink gãy) | `ffmpeg_real_master` |
| Đánh giá chất lượng (corpus/người chấm) | Không có corpus được cấp quyền, không có reviewer | `quality_evaluation_corpus` |
| Vector index | Cần embedding do người dùng nhập | `vector_index` |
| Auto-approve | Chưa có evidence chất lượng | `auto_approve_translation` |
| Public export bypass | Bị chặn có chủ đích (C08) | `public_export_bypass` |

### 3.2 Dòng G-PERF chưa đo được

| Dòng | Trạng thái | Lý do |
|---|---|---|
| long task khi gõ (fixture **EDITOR8TAB**, không phải C2K) | **NOT_RUN** | Cần browser thật với PerformanceObserver; harness chỉ có Python + Node headless. `perf-c2k.json` không có dòng NOT_RUN nào |
| SSE 30 phút — heap phút 30 (fixture **SSE30MIN**) | **NOT_RUN** | Phiên đo **rút ngắn còn 90,1 s**, không đủ 1.800 s. Số đo rút ngắn: RSS +15,22 MiB, heap Node sau GC +0,17 MiB |

### 3.3 Giới hạn thiết kế đã biết

- **Draft delta sống giữa job**: SQLite một-writer nên draft commit cùng transaction của run;
  UI thấy nháp sau khi job ghi xong (U07/J04).
- **Job retry/cancel qua UI**: **đã nối xong** — `POST /api/jobs/{id}/cancel` và `/retry` là wrapper mỏng trên
  `JobRunner.request_cancel`/`retry_failed`; `useJobActions` trong `router.tsx` truyền vào `JobsList` và `BatchQueue`,
  sau hành động UI đọc lại snapshot (thay thế buffer) nên không hiện trạng thái giả. Chỉ **E2E SSE trên browser** còn NOT_RUN.
- **REVIEW/REPAIR/SUMMARIZE qua cloud**: handler đã nối nhưng nhánh cloud **fail rõ ràng**
  (`REVIEW_CLOUD_PROVIDER_NOT_WIRED`, `SUMMARIZE_REQUIRES_CLOUD_PROVIDER`) thay vì giả thành công.
- **Backup incremental chỉ cho artifact**: file DB vẫn là bản sao đầy đủ qua SQLite Online Backup API.
- **SQLite DDL không transactional**: migration lỗi giữa đường rollback hàng version nhưng để lại DDL.
  Phát hiện bằng so revision-vs-head; phục hồi bằng restore từ backup đã verify.

## 4. Kết luận

**Phạm vi phát hành Phase 1 là `fixture-verified` — KHÔNG phải production-validated.**

- Toàn bộ đường chức năng chính chạy được **offline/không cloud**: import → dịch (fake) → duyệt →
  render giọng (fake) → duyệt audio → export bundle, có browser E2E phủ.
- Mọi tích hợp cloud/model thật **chưa được kiểm định**; các tính năng tương ứng **đang bị tắt** và được
  liệt kê ở mục 3.1.
- Không có tuyên bố nào trong tài liệu này dựa trên fake được suy diễn thành chất lượng thật.

**Bước tiếp theo để đạt live-verified**: cấp credential + consent + budget cloud, cài model VieNeu + license,
cài FFmpeg hoạt động, chuẩn bị corpus có quyền + người review, rồi chạy lại G-LIVE theo `release-gate.md`.
