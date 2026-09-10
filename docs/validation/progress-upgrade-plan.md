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
| U04 Draft API và optimistic concurrency | DONE (code) | R1 backend: bảng `workspace_drafts` (migration 0015, UNIQUE project/chapter/base revision) + compare-and-swap (`DRAFT_REVISION_CONFLICT`), validate segment/size cap, không đụng approved, API `GET/PUT /api/chapters/{id}/draft` (200/409/400) — 6 test. R2 seam: `append_draft_delta` (duplicate/gap/`commit=False`) dùng bởi worker (J04 R4) — 5 test. R3 editor: `useChapterDraft` + `DraftControls` khôi phục nháp theo base revision, gửi `expectedRevision`, 409 giữ nguyên bản đang sửa + diff theo segment + chọn ghi đè/dùng bản server, retry idempotent khi nội dung trùng — 9 test |
| U01 Design system/tokens/primitives | DONE (code) | 84341ae, 5f25a46, 10c0037, 4d4aa90, 53d4377; 24 shared/ui tests; **visual/zoom/screen-reader audit NOT_RUN** (cần browser harness) |
| U05 Tabs, dock và lưu layout | PARTIAL | Round 1: `workspaceLayout.ts` — reducer thuần (8 tab/pane; mở/đóng/reorder/split/dock/undock/reset), tab thứ 9 chỉ evict tab **sạch** (dirty → `TAB_LIMIT_DIRTY`), đóng tab dirty bị chặn (`TAB_DIRTY`) trừ `force`, `undock` từ chối khi tràn (`UNDOCK_OVERFLOW`), guard cross-project (`PROJECT_MISMATCH`), serialize/parse có version + sanitize — 12 test. Round 2: bảng `workspace_layouts` (migration 0016, UNIQUE project), service `load_layout`/`save_layout` (CAS `LAYOUT_REVISION_CONFLICT`, sanitize bỏ tab project khác + cap 8 tab/pane, version lạ → `migrated`), API `GET/PUT /api/projects/{id}/workspace-layout` (200/409/400/422/404) — 9 test; client `useWorkspaceLayout` (khôi phục theo project, `migrated` → layout mặc định, dispatch cục bộ + lưu CAS, 409 giữ layout local + reload) — 6 test. Còn: UI dock/tab thật + E2E keyboard/reopen, kiểm responsive 320/390/1024/1366 |
| U02 App shell & Settings bảy nhóm | PARTIAL | P1 cây `/settings` 7 nhóm (4 test); P2 `GlobalNav` ba khu vực (3 test); P3 nested `projects/:projectId/settings` lấy project từ URL, nhóm translation render 4 manager theo project (3 test). Còn: tách global IA/Thư viện với nested project routes, nội dung TTS/Storage/Appearance, keyboard route tests |
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
- M4: J03 DONE; J01/J02/J04 PARTIAL (nhiều lát cắt commit + test).
- M5: U01 code-complete (kiểm trực quan NOT_RUN); U04 DONE (code); U05 round 1 (reducer); U02/U08/U09 đang PARTIAL; U03/U06/U07/U10 chưa thực hiện.
- M6: A01 code-verified (live NOT_RUN); A02–A05 chưa thực hiện.
- M7/M8: chưa thực hiện (R02 là cổng phát hành; X-task không chặn Phase 1).

## Cam kết trạng thái

- Không task nào bị đánh DONE khi chưa đủ evidence; các acceptance phụ thuộc môi trường live
  (cloud trả phí, model VieNeu cài máy, corpus có quyền, reviewer/người nghe, browser visual) được ghi
  NOT_RUN/BLOCKED và feature tương ứng giữ disabled, không claim live-verified.
- Toàn bộ thay đổi trong tiến trình này đều có diff/commit riêng + command/exit code như trên.

## Kết luận phiên làm việc này

Tiến trình đạt: M0–M3 hoàn chỉnh, M4 mở đầu (J01 3 lát cắt, J02 2 lát cắt), U01 code-complete, A01
code-verified; backend 623 passed, frontend 46 passed, build PASS, tree sạch sau mỗi commit. Phần còn lại
(J01/J02 nốt acceptance, J03–J04, U02–U10, A02–A05, E01, V01–V03, R01–R03, X01–X07) là khối lượng lớn
gồm nhiều task yêu cầu môi trường live (cloud có quyền, model VieNeu/giọng, corpus, reviewer, browser
visual) chưa thể hiện thực và kiểm định trong giới hạn phiên; trạng thái từng task được giữ chính xác ở
ma trận trên và không bị đánh dấu hoàn thành vượt bằng chứng.
