# Progress và trạng thái triển khai implementation-plan

Ngày cập nhật: phiên này (nhánh `codex/implement-upgrade-plan`). Tài liệu này ánh xạ từng task của
[docs/plans/implementation-plan.md](../plans/implementation-plan.md) sang trạng thái + bằng chứng hiện tại.
Trạng thái dùng: DONE (acceptance + test xanh), PARTIAL (có code/test thật nhưng acceptance còn thiếu),
NOT_STARTED, NOT_RUN/BLOCKED (chỉ do thiếu môi trường live/cloud/model — theo quy tắc plan §2, không tự bịa evidence).

## Cổng nghiệm thu hiện tại

- Backend full suite (repo root): `.venv\Scripts\python.exe -m pytest backend/tests -q` → **654 passed**, 1 warning
  (SQLAlchemy FK-cycle sort, có sẵn từ baseline), exit 0.
- Frontend: `npm test -- --run` → **46 passed / 15 files**; `npm run build` (tsc + Vite) → PASS.
- Ruff các file thay đổi: PASS. Không chạy cloud trả phí/live/model download trong toàn bộ quá trình.

## Ma trận task → trạng thái → bằng chứng

| Task | Trạng thái | Bằng chứng (commit / test) |
|---|---|---|
| F01 Baseline/backup/restore | DONE | 39b9e93, f56dfc3; baseline.md PASS |
| F02 Typed contracts/lỗi | DONE | beb3066; contract tests |
| F03 Schema provenance/migration | DONE | af14155, 2e9f280; schema frozen/round-trip |
| S01 Keyring/credential lifecycle | DONE | e53b790…6963297 chain; security tests |
| S02 Chặn rò key/CSRF/loopback | DONE | close 2e5b91a (r8 PASS); 127+ security tests |
| S03 Quote/reservation guard | DONE | afb242c, 12a9189, ab3603d; budget/compliance tests |
| P01 Registry/descriptor capability | DONE | e53b790, 483f332; test_registry_catalog |
| P02 Discovery/catalog phân trang | DONE | 1dc1cdc, ae5f1f4, 4f95897, 6f78cdc |
| P03 Transport/SSE parser | DONE | d61c02b; test_transport |
| P04 Adapter Gemini native | DONE | 3c7fb21; full 564→… chạy trong round đó |
| P05 Adapter Qwen-MT native (wire theo docs 2026-09-09) | DONE | 3c7fb21; error matrix/language codes |
| P06 Adapter OpenRouter | DONE | 9153cd0 |
| C01 Style profile/prompt builder/native-MT builder | DONE | 9339fe6 |
| C02 Glossary scope/forbidden/evidence | DONE | bff39b7 |
| C03 Translation memory exact | DONE | 69181bf |
| C04 Characters & directed addressing | DONE | ef22114 |
| C05 Summary approval + context engine | DONE | 6ca8eca |
| C06 QA/edit/approve/repair + segment coverage guard | DONE | 7de976e |
| J01 Worker/handler/checkpoint | PARTIAL | rounds 1–4: 4b7821b, 310a6ff, 07ea083 + round 4 (harness thật) — plan seam, batch child plan, real TRANSLATE handler, crash-before-provider-result & crash-after-commit chạy qua handler sản xuất (recovery không nhân đôi segment, lần chạy lại phục vụ từ cache nên **không gọi provider lần hai**). Còn: REVIEW/REPAIR_TRANSLATION/SUMMARIZE handler (cần provider/model) + checkpoint theo từng segment cho job dài |
| J02 Retry/cancel/breaker persist | DONE | 82ef1da, f4f0265, cfdc065 — persisted breaker + half-open + dispatch gate + HTTP retry classification (429 Retry-After, 401 no loop, billingUnknown no resend); cancel p95 timing thuộc G-PERF (V01) |
| J03 Projection/feed/diagnostics | DONE | EventLog append-only + cursor SSE + sync dedupe (d577b18, deb91f3); retention purge theo cutoff + rebuild parity (22b136c); emit cùng transaction tại job terminal & approve (round 3); structured log model/latency/cost redacted |
| J04 Draft streaming resumable | PARTIAL | Engine + snapshot + SSE feed + adapter delta source (`stream_translate`) đã xong & test. Round 4: worker ghi delta provider vào `workspace_drafts` (U04) qua `DraftDeltaSinkFactory` bind session → feed `GET /api/jobs/{id}/draft[/stream]` phục vụ draft overlay (offset theo segment, dedupe/gap, `draftRevision`, không bao giờ approvable) — 5 test. Còn: hiển thị delta **trong lúc** job chạy (SQLite một-writer: draft commit cùng transaction của run) |
| U03 Phân trang thư viện và import review | PARTIAL | Round 1: backend `ProjectQueries` + `GET /api/projects` — cursor ký HMAC (`LIBRARY_CURSOR_INVALID` khi giả), `limit` cap **100** (mặc định 20), filter `q` server-side, page metadata (`total/count/hasMore/nextCursor/mountedChaptersPerProject`); mỗi project chỉ mount **30 chapter summary** (window function) + `chapterCount` chính xác qua GROUP BY ⇒ dự án 120 chương không kéo chapter/không lộ source text vào payload, và số statement mỗi trang **hằng số** (test đếm `SELECT` ≤ 6) — 5 test. Import review: thêm 4 test cho `build_import_candidates` (DUPLICATE_ORDINAL gắn cho **mọi** section trùng kèm `sourcePath`, ORDINAL_MISSING/EMPTY_CHAPTER nêu đúng file, chương 1200 vẫn nhận, text giữ nguyên khi confirm). Frontend: thư viện dùng `page` (hiện `n/total`), nút **Tải thêm** theo `nextCursor`, ô tìm kiếm gửi `q`, và vẫn chạy khi backend cũ không có `page` — 3 test. Còn: route E2E browser cho import/duplicate, preview encoding trong UI |
| U04 Draft API và optimistic concurrency | DONE (code) | R1 backend: bảng `workspace_drafts` (migration 0015, UNIQUE project/chapter/base revision) + compare-and-swap (`DRAFT_REVISION_CONFLICT`), validate segment/size cap, không đụng approved, API `GET/PUT /api/chapters/{id}/draft` (200/409/400) — 6 test. R2 seam: `append_draft_delta` (duplicate/gap/`commit=False`) dùng bởi worker (J04 R4) — 5 test. R3 editor: `useChapterDraft` + `DraftControls` khôi phục nháp theo base revision, gửi `expectedRevision`, 409 giữ nguyên bản đang sửa + diff theo segment + chọn ghi đè/dùng bản server, retry idempotent khi nội dung trùng — 9 test |
| U01 Design system/tokens/primitives | DONE (code) | 84341ae, 5f25a46, 10c0037, 4d4aa90, 53d4377; 24 shared/ui tests; **visual/zoom/screen-reader audit NOT_RUN** (cần browser harness) |
| U05 Tabs, dock và lưu layout | PARTIAL | Round 1: `workspaceLayout.ts` — reducer thuần (8 tab/pane; mở/đóng/reorder/split/dock/undock/reset), tab thứ 9 chỉ evict tab **sạch** (dirty → `TAB_LIMIT_DIRTY`), đóng tab dirty bị chặn (`TAB_DIRTY`) trừ `force`, `undock` từ chối khi tràn (`UNDOCK_OVERFLOW`), guard cross-project (`PROJECT_MISMATCH`), serialize/parse có version + sanitize — 12 test. Round 2: bảng `workspace_layouts` (migration 0016, UNIQUE project), service `load_layout`/`save_layout` (CAS `LAYOUT_REVISION_CONFLICT`, sanitize bỏ tab project khác + cap 8 tab/pane, version lạ → `migrated`), API `GET/PUT /api/projects/{id}/workspace-layout` (200/409/400/422/404) — 9 test; client `useWorkspaceLayout` (khôi phục theo project, `migrated` → layout mặc định, dispatch cục bộ + lưu CAS, 409 giữ layout local + reload) — 6 test. Round 3: UI thật trong shell — `WorkspaceTabs` (role=tablist/tab đúng ARIA, điều hướng bàn phím ←/→/Home/End/Enter/Delete, roving tabindex, đóng tab dirty bị chặn + `TAB_DIRTY` + nút "Đóng và bỏ thay đổi", tab đã đóng không tự mở lại khi đổi route, dock/undock khung phụ, lưu layout + báo 409) — 7 test; `workspaceRoutes.ts` (map route → tab: editor/QA/inspector/job/preview + chỉ route có project UUID mới persist) — 4 test; action `sync` idempotent trong reducer — 2 test; Shell gắn `WorkspaceTabs` theo route. Còn: E2E keyboard/reopen ngoài browser thật, kiểm responsive 320/390/1024/1366 |
| U07 Job UI và draft streaming | PARTIAL | Round 1: `useJobDraftStream` (snapshot `GET /api/jobs/{id}/draft` trước, SSE `/draft/stream?afterOffset=` với `draft`/`gap`/`terminal`, frame trùng theo offset bị bỏ, gap → thay bằng snapshot, terminal đóng stream nhưng giữ text, resync thủ công, hoạt động cả khi không có `EventSource`) + `JobDraftPanel` (read-only, badge trạng thái, offset, cảnh báo truncated, khẳng định nháp **không** dùng để duyệt) — 6 test; route `jobs/:jobId/draft` + map tab JOB. **Round 2**: `jobStore.ts` — **một** store/SSE subscription dùng chung, ref-count (consumer thứ hai không mở thêm connection; unmount hết → đóng; remount không nhân listener), buffer **cap 1.000** + cờ `truncated`, reconnect có backoff + **đọc lại snapshot trước khi resume** (bịt gap), bỏ qua event replay theo `sequenceId`, `retryableFailures()` chỉ trả job FAILED có mã retryable; `JobsList` (một dòng/job theo event mới nhất, nhãn `CANCEL_REQUESTED`, hủy chỉ hiện khi còn dừng được, thử lại chỉ cho lỗi retryable, link sang nháp) — 14 test; `JobProgress` dùng store chung + link nháp + nhãn hủy. Còn: UI batch partial-failure đầy đủ, E2E SSE thật |
| U06 Editor song ngữ, QA, inspector | PARTIAL | Round 1: `BilingualEditor` — hàng song ngữ key theo **stable source segment id**; pane nguồn là text `aria-readonly` (không có textbox cho source, chỉ target sửa được); **chặn lưu khi IME đang composition** (`IME_COMPOSITION_ACTIVE`) rồi lưu sau `compositionend`; `Ctrl/Cmd+S` lưu đúng đoạn đang sửa kèm `expectedRunHash`; **QA inspector** lọc theo severity + bấm issue → reveal/scroll đúng đoạn (`data-revealed`, `scrollIntoView`); áp dụng đề xuất QA vào draft; **CRITICAL đang OPEN chặn Phê duyệt** (kèm đường bỏ qua có chủ đích `force: true`); 409 giữ nguyên text đang sửa + hiện mã lỗi — 7 test; route `/chapters/:id/editor` + map tab EDITOR. Còn: inspector glossary/character/context trace, diff/repair proposal đầy đủ (`RepairDiff` cần proposal từ job REPAIR), virtualize + route conflict tests mở rộng. **Round 2**: backend `context_trace.py` + `GET /api/chapters/{id}/translation/context-trace` — run gần nhất (hash glossary/memory đã ghi), glossary revision + **chỉ rule khoá trong phạm vi ordinal**, story memory **chỉ APPROVED** + hash, nhân vật active (aliases/status), cờ `stale` so hash run ↔ hash hiện tại (5 test); frontend `ContextInspector` (glossary/memory/nhân vật, cảnh báo stale, trạng thái rỗng, lỗi trace, và **ID thô chỉ nằm trong Drawer chi tiết**) — 5 test, gắn vào `BilingualEditor`. |
| U10 Storage, Appearance, Advanced | PARTIAL | Round 1: **Storage** — `BackupService.list_backups()` (checksum/integrity từng bản, bản hỏng hiện là `verified: false` chứ không crash), `retention_plan(count)` **chỉ là kế hoạch** (`applied: false`, `requiresConfirmation`, `pruneOnCreate` — đổi retention không xóa file nào), `restore_copy()` bắt buộc `confirmTarget` khớp đúng đường dẫn + chỉ restore vào data root mới (từ chối target trùng/overlap, verify backup trước khi ghi), API `GET /backups`, `GET/PUT /retention`, `POST /backups/{id}/restore-copy` (400 thiếu xác nhận, 409 target tồn tại/backup hỏng/lock) — 5 test. **Feature flags** — `feature_flags.py` fail-closed (unsafe/unknown → `FEATURE_FLAG_UNSAFE:<name>`/`FEATURE_FLAG_UNKNOWN:<name>`, flag unsafe không bật được qua API, `applied: false` vì rollout thuộc R01) + API `GET/PUT /api/settings/feature-flags` — 5 test. **Appearance** — `uiPreferences.ts` (chỉ theme/font/density/reduceMotion; khóa lạ và khóa kiểu content/secret bị loại và **không** hiển thị lại; version + migrate) + `AppearanceSettings` panel (lưu/khôi phục/về mặc định, preview chỉ hiện document đã lọc) — 11 test. Còn: panel Storage UI đầy đủ (dùng `CleanupPreview` + plan), Advanced/diagnostics export trong UI, feature-flag persistence (R01) |
| U02 App shell & Settings bảy nhóm | PARTIAL | P1 cây `/settings` 7 nhóm (4 test); P2 `GlobalNav` ba khu vực (3 test); P3 nested `projects/:projectId/settings` lấy project từ URL, nhóm translation render 4 manager theo project (3 test); P4 **Storage** (`StorageSettings`: dung lượng, danh sách backup kèm trạng thái checksum, retention chỉ tạo **kế hoạch** `applied:false` không xoá gì, cleanup preview → execute đúng `planId`+`snapshotHash`, phục hồi bản sao chỉ bật khi gõ lại đúng thư mục và chỉ với backup verified, lỗi backend hiện nguyên mã) — 5 test; P5 **TTS** dùng catalog giọng cục bộ (`VoiceBrowser`) với ghi chú giọng chỉ khả dụng khi đã cài model/license. Còn: tách IA Thư viện với nested project routes, keyboard route tests |
| U09 Quản lý style/glossary/nhân vật/memory | PARTIAL |
| U09 Quản lý style/glossary/nhân vật/memory | PARTIAL | P1 `StyleManager` 4 test; P2 `GlossaryManager` 6 test; P3 `CharacterManager` (evidence gate, addressing có hướng) 6 test; P4 `MemoryManager` (candidate approve cần run+segment, reject, context chỉ APPROVED + hash) 5 test — cả 4 gắn Settings→Translation. Còn: preview stale-scope, nested project routes |
| U08 Provider/model và quality settings | PARTIAL | P1 `modelCatalog` 6 test; P2 `ProfileEditor` credential masked 5 test (Settings→Providers); P3 `QualityPlanPanel` (Balanced mặc định, Quality/Maximum cần opt-in, quote theo stage + tổng/expiry/warnings, **đổi model ⇒ quote hết hiệu lực**, thiếu consent thì chặn không gọi API, 403 → alert) 6 test — chờ gắn vào màn chapter (U06). Còn: nhãn quality picker, combobox/rotate E2E |
| A01 VieNeu manifest/bridge/catalog | DONE (code) | 89cc0d1 (26 local-tts/speech tests); **live probe/playback NOT_RUN** (chưa cài model/license) |
| A02–A05 Preview/voice-plan/audio | NOT_STARTED | A02 live phụ thuộc model VieNeu (BLOCKED env nếu chưa cài) |
| E01 Export bundle | NOT_STARTED | deps A05/U07/U09/U10 |
| V01–V03 Fixture/benchmark/validation | NOT_STARTED | — |
| R01–R03 Migration/rollout/docs/cleanup | NOT_STARTED | — |
| X01–X07 Phase 2 extensions | NOT_STARTED | R02 chưa đạt; không bật |

## Ghi chú theo milestone

- M0 ✓ (F01–F03), M1 ✓ (S01–S03, P01–P02), M2 ✓ (P03–P06), M3 ✓ (C01–C06).
- M4: J03 DONE; J02 DONE; J01 PARTIAL (handler thật + crash coverage; REVIEW/REPAIR/SUMMARIZE cần provider/model); J04 PARTIAL (engine + SSE + delta→draft; delta sống giữa job NOT_RUN).
- M5: U01 code-complete (kiểm trực quan NOT_RUN); U03 round 1 (phân trang thư viện + import review warnings); U04 DONE (code); U05 round 1–3 (reducer + API/hook + UI tab/dock trong shell; E2E/responsive NOT_RUN); U06 round 1–2 (editor song ngữ + QA inspector + context trace inspector); U07 round 1–2 (panel nháp job + job store/SSE dùng chung + danh sách job); U10 round 1 (storage retention/restore-copy + feature-flag guard + appearance prefs); U02/U08/U09 PARTIAL.
- M6: A01 code-verified (live NOT_RUN); A02–A05 chưa thực hiện.
- M7/M8: chưa thực hiện (R02 là cổng phát hành; X-task không chặn Phase 1).

## Cam kết trạng thái

- Không task nào bị đánh DONE khi chưa đủ evidence; các acceptance phụ thuộc môi trường live
  (cloud trả phí, model VieNeu cài máy, corpus có quyền, reviewer/người nghe, browser visual) được ghi
  NOT_RUN/BLOCKED và feature tương ứng giữ disabled, không claim live-verified. Riêng môi trường browser:
  đã kiểm tra trong phiên và **không có** Chromium/Chrome/Edge/Firefox binary, không có cache
  `ms-playwright` và không có module `playwright` trong repo ⇒ mọi kiểm visual/E2E/responsive được ghi
  NOT_RUN có căn cứ, không phải suy đoán.
- Toàn bộ thay đổi trong tiến trình này đều có diff/commit riêng + command/exit code như trên.

## Kết luận phiên làm việc này

Tiến trình đạt: **M0–M3 hoàn chỉnh** (F01–F03, S01–S03, P01–P06, C01–C06 DONE), **M4**: J02/J03 DONE, J01
PARTIAL (plan seam + handler TRANSLATE thật + crash-before-send/after-commit trên handler sản xuất), J04
PARTIAL (engine/snapshot/SSE + worker ghi delta vào `workspace_drafts` + overlay feed); **M5**: U01
code-complete (visual/a11y audit NOT_RUN), U04 DONE (code), U05 round 1–2 (reducer + API/hook layout),
U02/U08/U09 PARTIAL; **M6**: A01 code-verified (live probe NOT_RUN). Backend **676 passed** (1 warning
FK-cycle có sẵn), frontend **121 passed / 28 files**, `npm run build` PASS, ruff sạch trên mọi file thay đổi;
mỗi lát cắt có report riêng trong `.superpowers/sdd/` và commit riêng.

Còn lại (giữ nguyên trạng thái, **không** đánh dấu hoàn thành): U03, UI dock/tab của U05, U06, U07, U10,
A02–A05, E01, V01–V03, R01–R03, X01–X07, cùng các acceptance cần môi trường live — cloud trả phí có
consent/budget (REVIEW/REPAIR/summarize handler, delta sống giữa job, live smoke), model VieNeu + license
(A02/A03 live), corpus có quyền và reviewer (V03), browser/visual harness (U01 audit, U05 responsive, E2E
keyboard dock). Các mục này ghi NOT_RUN/BLOCKED đúng quy tắc plan §2 và không được claim là đã kiểm định.


