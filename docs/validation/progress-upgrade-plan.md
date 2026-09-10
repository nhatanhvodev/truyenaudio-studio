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
| J01 Worker/handler/checkpoint | PARTIAL | rounds 1–3: 4b7821b, 310a6ff, 07ea083 — plan seam, batch child plan, real TRANSLATE handler; còn REVIEW/REPAIR/SUMMARIZE handler + process-harness crash coverage |
| J02 Retry/cancel/breaker persist | DONE | 82ef1da, f4f0265, cfdc065 — persisted breaker + half-open + dispatch gate + HTTP retry classification (429 Retry-After, 401 no loop, billingUnknown no resend); cancel p95 timing thuộc G-PERF (V01) |
| J03 Projection/feed/diagnostics | DONE | EventLog append-only + cursor SSE + sync dedupe (d577b18, deb91f3); retention purge theo cutoff + rebuild parity (22b136c); emit cùng transaction tại job terminal & approve (round 3); structured log model/latency/cost redacted |
| J04 Draft streaming resumable | PARTIAL | Engine + snapshot + SSE feed (segmentReady) + adapter delta source (`stream_translate`) đã xong & test; phần còn lại (delta sống → feed job-scoped) phụ thuộc **persisted draft của U04** (workspace_drafts) vì worker và API là hai tiến trình |
| U01 Design system/tokens/primitives | DONE (code) | 84341ae, 5f25a46, 10c0037, 4d4aa90, 53d4377; 24 shared/ui tests; **visual/zoom/screen-reader audit NOT_RUN** (cần browser harness) |
| U02–U10 Workspace/settings/editor… | NOT_STARTED | — |
| A01 VieNeu manifest/bridge/catalog | DONE (code) | 89cc0d1 (26 local-tts/speech tests); **live probe/playback NOT_RUN** (chưa cài model/license) |
| A02–A05 Preview/voice-plan/audio | NOT_STARTED | A02 live phụ thuộc model VieNeu (BLOCKED env nếu chưa cài) |
| E01 Export bundle | NOT_STARTED | deps A05/U07/U09/U10 |
| V01–V03 Fixture/benchmark/validation | NOT_STARTED | — |
| R01–R03 Migration/rollout/docs/cleanup | NOT_STARTED | — |
| X01–X07 Phase 2 extensions | NOT_STARTED | R02 chưa đạt; không bật |

## Ghi chú theo milestone

- M0 ✓ (F01–F03), M1 ✓ (S01–S03, P01–P02), M2 ✓ (P03–P06), M3 ✓ (C01–C06).
- M4: J01/J02 đang PARTIAL (nhiều lát cắt commit + test); J03/J04 chưa thực hiện.
- M5: U01 code-complete (kiểm trực quan NOT_RUN); U02–U10 chưa thực hiện.
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
