# G-RELEASE — Báo cáo cổng phát hành Phase 1 (task R01)

- **Thời điểm chạy:** 2026-09-10T14:05:32Z → 2026-09-10T14:06:53Z (UTC)
- **Commit:** `dcf92c3adfa41ef8578af2bc55d13dd730b58427` (nhánh `codex/implement-upgrade-plan`)
- **Máy:** Windows 11 (10.0.26200) · Python 3.12.13 · Node v24.11.1 · RAM 7,4 GB · data root repo `data/`
- **Phạm vi phát hành: `fixture-verified`.** Đây **không phải** báo cáo kiểm định production.
- **Nguyên tắc:** không gọi cloud, không tải model, không bịa evidence; **NOT_RUN không bao giờ được tính là PASS**.
  Mọi dòng dưới đây có command, exit code, timestamp và commit thật; dòng nào không chạy được thì ghi NOT_RUN/BLOCKED
  kèm lý do cụ thể và feature tương ứng **đang disabled**.

---

## 1. Lệnh đã chạy (command · exit code · timestamp · commit)

| # | Lệnh | Exit | Bắt đầu (UTC) | Kết quả tóm tắt |
|---|---|---|---|---|
| 1 | `.venv\Scripts\python.exe -m pytest backend/tests/db backend/tests/storage -q` | **0** | 2026-09-10T14:05:32Z | `59 passed, 1 warning in 59.82s` |
| 2 | `.venv\Scripts\python.exe -m ruff check scripts backend/app/modules/settings backend/app/db backend/tests/db` | **1** | 2026-09-10T14:06:34Z | `Found 5 errors` — **tất cả nằm ở `scripts/crawl_wenku.py`** (F401 ×3, F541 ×2), file có trước R01, ngoài phạm vi sở hữu; xem §1.1 |
| 3 | `.venv\Scripts\python.exe -m ruff check <các path R01>` | **0** | 2026-09-10T14:06:34Z | `All checks passed!` |
| 4 | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/preflight.ps1` | **1** | 2026-09-10T14:06:35Z | `PREFLIGHT FAIL: ffmpeg … could not be executed` — FFmpeg của máy này **hỏng thật** (symlink WinGet gãy, xem §1.2) |
| 5 | `powershell … scripts/preflight.ps1 -SkipFfmpeg` | **0** | 2026-09-10T14:06:36Z | `Migrations: 18 (head 0018); database at 0004; state MIGRATION_INCOMPLETE` → `PREFLIGHT OK` |
| 6 | `powershell … scripts/preflight.ps1 -VerifyDatabase -SkipFfmpeg` (data root repo) | **1** | 2026-09-10T14:06:38Z | `PREFLIGHT FAIL: the database is at 0004 but head is 0018 …` (chặn khởi động trên DB cũ — hành vi đúng) |
| 7 | `scripts/migrate.ps1` rồi `preflight.ps1 -VerifyDatabase -SkipFfmpeg` trên data root TẠM trống | **0** / **0** | 2026-09-10T14:06:39Z | `MIGRATE before: DATABASE_MISSING` → `MIGRATE after: revision=0018 head=0018 migrations=18 status=OK`; verify: `Database is at head (0018)` |
| 8 | `.venv\Scripts\python.exe -m pytest backend/tests/db/test_migration_rehearsal.py backend/tests/storage/test_backup_rollback_drill.py -q -s` | **0** | 2026-09-10T14:06:43Z | `6 passed in 7.67s` + 6 dòng `EVIDENCE` (trích ở §2) |

Commit cho mọi dòng: `dcf92c3adfa41ef8578af2bc55d13dd730b58427`. Vùng test chỉ chạy hẹp `backend/tests/db`,
`backend/tests/storage`, `backend/tests/settings` và `backend/tests/fixtures` — **không chạy full suite**.

### 1.1 Vì sao lệnh 2 exit 1

5 lỗi ruff đều nằm trong `scripts/crawl_wenku.py` (import thừa `os`, `typing.Tuple`, `urlparse`; 2 f-string không có
placeholder). File này không thuộc danh sách file R01 sở hữu và không liên quan migration/rollout; sửa nó nằm ngoài
phạm vi task. Lệnh 3 chạy đúng các path R01 và **pass sạch** — nên đây là nợ lint có trước, được ghi nhận thay vì che đi.

### 1.2 Vì sao lệnh 4 exit 1 (và vì sao nó không bị hạ xuống PASS)

`Get-Command ffmpeg` trỏ tới `C:\Users\nhata\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe`, một **symlink gãy**:
target `…\Gyan.FFmpeg_…\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe` **không tồn tại** (`Test-Path` = False).
Vì vậy preflight fail-closed với thông báo rõ ràng và exit 1 — đúng yêu cầu "FAIL RÕ RÀNG khi thiếu điều kiện".
**Hệ quả:** mọi tính năng cần FFmpeg thật (render/master audio thật) giữ **NOT_RUN + disabled** (§7), không được gắn PASS.

---

## 2. Diễn tập R01 (tiêu chí nghiệm thu: DB cũ đọc được · rollback restore đúng checksum)

Toàn bộ bằng chứng dưới đây in ra từ chính tiến trình test (`-s`), không phải mô tả lại:

```text
EVIDENCE clean-install revision=0018 migrations=18 tables=37 integrity=ok
EVIDENCE legacy-upgrade from=0016 to=0018 rows_before={'projects': 1, 'chapters': 3, 'source_revisions': 3, 'source_segments': 6, 'translation_runs': 3, 'translation_segments': 6, 'artifacts': 3} rows_after={… y hệt …} new_table_rows=0 integrity=ok
EVIDENCE legacy-read-routes GET /api/projects, /api/projects/{id}, /api/projects/{id}/chapters, /api/chapters/{id}/translation -> 200
EVIDENCE fail-migration revision_stays=0018 head=0019 status=MIGRATION_INCOMPLETE rows_intact=True leftover_ddl=rehearsal_half_state retry_rejected=already exists
EVIDENCE backup-rollback backup=20260910T140651107056Z-945439e7 sha256=3979db13cc2c1661… byte_size=471040 integrity=ok content_digest_match=True rows_before={… 7 bảng …} restored_revision=0016 then_reupgraded=0018 artifact_sha256_match=True phases=['starting', 'database', 'artifacts', 'manifest', 'verifying', 'completed']
EVIDENCE restore-guards used_target_rejected=True corrupted_backup_rejected=True
```

| Diễn tập | File test | Kết quả | Bằng chứng cụ thể |
|---|---|---|---|
| **CLEAN INSTALL** — data root trống → `upgrade head` | `backend/tests/db/test_migration_rehearsal.py::test_clean_install_from_empty_data_root_reaches_head` | **PASS** | revision `0018`, 18 migration, **37 bảng**, `integrity_check=ok`; đối chiếu `Base.metadata` ⇒ không thiếu bảng ORM nào; `voice_preview_jobs` (0017) có mặt |
| **UPGRADE LEGACY FIXTURE** — fixture ở `0016` (trước 2 migration mới nhất) có dữ liệu thật → `upgrade head` | `test_upgrade_from_legacy_revision_preserves_rows_and_leaves_new_tables_empty` | **PASS** | Đếm hàng **trước = sau**: projects 1, chapters 3, source_revisions 3, source_segments 6, translation_runs 3, translation_segments 6, artifacts 3. Bảng mới rỗng (0 hàng) chứ không hỏng. Đọc lại qua ORM được (project/chapter/approved run + 2 target segment). `jobs.kind`: `SUMMARIZE` chèn được, `BOGUS` vẫn bị `IntegrityError` chặn ⇒ constraint rebuild của 0018 đúng |
| **LEGACY READ ROUTE** — data root đã nâng cấp, đọc bằng route thật | `test_upgraded_legacy_data_root_is_readable_through_real_read_routes` | **PASS** | `GET /api/projects` (title + `chapterCount=3`), `GET /api/projects/{id}`, `GET /api/projects/{id}/chapters` (total 3, ordinal 1..3), `GET /api/chapters/{id}/translation` (`run.status=APPROVED`, 2 segment) đều **200** |
| **BACKUP → UPGRADE → RESTORE + ROLLBACK** | `backend/tests/storage/test_backup_rollback_drill.py` | **PASS** | Backup ở `0016` → upgrade `head` (+ thêm job SUMMARIZE và voice-preview để bắt restore sai) → restore sang data root **MỚI**: `verify()` khớp lại sha256/byte_size của manifest, `integrity=ok`, **digest nội dung logic (`iterdump` sha256) trùng khít trước upgrade**, đếm hàng trùng, artifact khôi phục đúng sha256, revision trở về `0016`, và các hàng hậu-upgrade **biến mất**; sau đó upgrade lại `0016 → 0018` thành công |
| **FAIL MIGRATION** — migration lỗi giữa đường | `test_failed_migration_does_not_advance_revision_and_is_detected` | **PASS (phát hiện được)** | Xem §3 |
| **FEATURE FLAG ROLLBACK** — flag chỉ bật khi có evidence | `backend/tests/settings/test_release_manifest.py` (7 test) + `test_feature_flags.py` | **PASS** | Xem §5 |
| **Guard restore** — backup hỏng / target đã dùng | `test_restore_refuses_a_corrupted_backup_and_a_used_target` | **PASS** | Restore vào target đã tồn tại ⇒ `BackupError`; backup bị cắt cụt ⇒ `BackupVerificationError` và **không tạo** data root đích |

**Checksum/rollback — phát biểu chính xác:** backup **không** bị sửa (sha256 + byte_size tái xác nhận sau restore),
và DB khôi phục trùng với **nội dung logic** trước upgrade. Không claim "byte-identical": API online-backup của SQLite
viết lại file, nên byte-identity không phải là tiêu chí đúng; digest `iterdump` + đếm hàng + sha256 artifact mới là.

---

## 3. Phát hiện của diễn tập FAIL MIGRATION (bắt buộc đọc)

**Cách mô phỏng:** copy `backend/migrations` sang thư mục tạm (không sửa repo) và thêm revision `0019` có thật về
cấu trúc: `op.create_table("rehearsal_half_state", …)` → `INSERT` một hàng → `raise RuntimeError("REHEARSAL_FORCED_FAILURE")`.
Sau đó chạy `alembic upgrade head` trên DB fixture đang ở `0018`.

**Kết quả đo được (không suy luận):**

| Hiện tượng | Giá trị thật |
|---|---|
| Alembic log | `Will assume non-transactional DDL` (SQLite) |
| `alembic_version` sau lỗi | vẫn `0018` — **version row rollback đúng** |
| Bảng do migration tạo trước khi lỗi | **còn lại** (`rehearsal_half_state`) |
| Dữ liệu nghiệp vụ | không đổi (7 bảng đếm y hệt trước) |
| `PRAGMA integrity_check` | `ok` |
| Chạy lại migration đó | **thất bại** với `… already exists` — retry KHÔNG phải đường phục hồi |
| Phát hiện | `migration_status()` trả `MIGRATION_INCOMPLETE` (`db=0018`, `head=0019`) ⇒ `preflight.ps1 -VerifyDatabase` chặn khởi động |

**Kết luận vận hành:** SQLite DDL không transactional, nên "DB không ở trạng thái nửa vời" **không thể bảo đảm bằng
transaction**. Cơ chế bảo vệ của repo là: (a) phát hiện bằng so revision với head, (b) phục hồi bằng **restore từ backup
đã verify** (§2) hoặc migration sửa tiến về phía trước — **không** trỏ binary cũ vào DB đã nâng cấp và **không** chạy lại
migration đã hỏng. `scripts/migrate.ps1` cũng fail khi sau khi upgrade DB không ở head.

---

## 4. Legacy read route — có cần giữ route cũ không?

**Trả lời: không có route legacy/compat nào cần giữ; dữ liệu cũ đọc qua chính route hiện tại.** Bằng chứng grep:

- `grep -n "legacy|LEGACY|deprecated|/v1/|compat" backend/app --include=*.py` chỉ trả về:
  `app/modules/security/credentials.py:1` và `:69` — **legacy keyring reader** (`keyring:service/user`), không liên quan
  đọc dữ liệu; và các URL endpoint của provider (`googleapis.com/v1/…`, `openrouter.ai/api/v1/…`, …).
- Không có prefix route dạng `/api/v1` hay `/legacy`: 27 router đều dùng prefix `/api/…` hiện hành.
- Thay vì giữ route cũ, R01 chứng minh **route hiện tại đọc được dữ liệu tạo trước khi nâng cấp** — 4 route trả 200 với
  dữ liệu fixture 0016 (§2, dòng LEGACY READ ROUTE).

---

## 5. Rollout theo release manifest + feature flag (fail-closed)

`docs/validation/release-manifest.json` là bản ghi evidence dạng máy đọc. `app/modules/settings/feature_flags.py`
**chỉ cho bật** một flag khi manifest ghi `evidence == "PASS"` cho feature tương ứng:

- evidence `NOT_RUN` / `FAIL` / `BLOCKED` ⇒ bật qua API bị từ chối `FEATURE_FLAG_EVIDENCE_NOT_PASS:<tên flag>`;
- manifest **thiếu** hoặc **hỏng** ⇒ cũng không bật được (fail-closed), không suy diễn thành "không có gì chặn";
- flag `unsafe` vẫn bị `FEATURE_FLAG_UNSAFE:<tên>` chặn **kể cả khi manifest có PASS** (vector index, auto-approve,
  public export bypass);
- `GET /api/settings/feature-flags` trả về `evidence`, `evidenceFeature`, `evidenceRef`, `disabledReason` và trạng thái
  manifest; `PUT` vẫn **không** lưu bền vững (`applied: false`) — việc persist thuộc phạm vi rollout sau.

| Flag | Feature evidence | Trạng thái evidence | Hiệu lực |
|---|---|---|---|
| `workspace_tabs` | — (UI đã có test, đảo ngược được) | không yêu cầu | default, tắt/bật được |
| `draft_streaming` | — | không yêu cầu | default, tắt/bật được |
| `nested_project_settings` | — | không yêu cầu | default, tắt/bật được |
| `cloud_quality_mode` | `cloud_quality_live` | **NOT_RUN** | **FORCED OFF** + API trả 400 khi xin bật |
| `vector_index` | `vector_index_live` | **NOT_RUN** | **FORCED OFF** (và `unsafe`) |
| `auto_approve_translation` | `auto_approve_translation_live` | **NOT_RUN** | **FORCED OFF** (và `unsafe`) |
| `public_export_bypass` | `public_export_bypass_live` | **BLOCKED** | **FORCED OFF** (và `unsafe`) |

**Không provider nào được tự bật.** Không có code path nào trong R01 gọi provider/cloud hay tải model.

---

## 6. Bảng G-RELEASE

| Mục G-RELEASE (plan §7) | Trạng thái | Command / bằng chứng | Exit | Timestamp (UTC) | Commit |
|---|---|---|---|---|---|
| Migration clean install + upgrade legacy | **PASS** | lệnh 8, `test_migration_rehearsal.py` | 0 | 2026-09-10T14:06:43Z | `dcf92c3` |
| Restore/rollback đúng checksum | **PASS** | lệnh 8, `test_backup_rollback_drill.py` | 0 | 2026-09-10T14:06:43Z | `dcf92c3` |
| DB cũ đọc được | **PASS** | `test_upgraded_legacy_data_root_is_readable_through_real_read_routes` (4 route 200) | 0 | 2026-09-10T14:06:43Z | `dcf92c3` |
| Không bật provider chưa smoke/thiếu account | **PASS** | `tests/settings/test_release_manifest.py` + manifest §5 | 0 | 2026-09-10T14:05:32Z | `dcf92c3` |
| Gate DDL không nửa vời khi migration lỗi | **PASS (phát hiện + phục hồi)** | §3 | 0 | 2026-09-10T14:06:43Z | `dcf92c3` |
| Preflight/runner phản ánh migration hiện tại và FAIL rõ ràng | **PASS** | lệnh 5/6/7 | 0 / 1 / 0 · 0 | 2026-09-10T14:06:36Z–14:06:43Z | `dcf92c3` |
| **G-CORE** (toàn pipeline E2E) | **NOT_RUN (R01 không chạy lại)** | Không chạy full backend suite / Playwright trong task này. Bằng chứng E2E trước đó nằm ở `docs/validation/progress-upgrade-plan.md` và `frontend/e2e/*.spec.ts` (9 spec, gồm `workspace-tabs`, `audio-range`, `export-review`, `visual-a11y`) | — | — | — |
| **G-PERF** | **NOT_RUN — không đạt đủ** | 14 fixture offline trong `docs/validation/perf-*.json`, **tất cả `overallStatus = PASS` nhưng 2 dòng bên trong là NOT_RUN**: (a) `perf-editor8tab.json` → "long task khi gõ >50 ms" **NOT_RUN** (cần browser thật, harness không có render engine); (b) `perf-sse30min.json` → "phiên đủ 30 phút + heap phút 30" **NOT_RUN** (phiên đo bị rút còn 90,1 s so với 1.800 s plan yêu cầu). **Không suy PASS cho 2 mục này** | — | — | `dcf92c3` |
| **G-UX** | **NOT_RUN (R01 không chạy lại)** | Audit browser/contrast trước đó thuộc U01/V02; R01 không chạy Playwright | — | — | — |
| **V03 report tồn tại dù NOT_RUN** | **PASS (đúng yêu cầu plan)** | `docs/validation/quality.md` — 10/10 mục NOT_RUN kèm probe môi trường | — | 2026-09-10T07:55:01Z | `4e94fdd` |
| **G-LIVE** | **NOT_RUN / BLOCKED** | `quality.md` §1: 0 provider profile, 0 consent, 0 budget authorization, 0 rate card, 0 usage ledger, không model TTS, không reviewer | — | — | — |
| Preflight nghiêm (FFmpeg bắt buộc) | **FAIL** | lệnh 4 — symlink WinGet gãy | 1 | 2026-09-10T14:06:35Z | `dcf92c3` |
| Ruff lệnh bắt buộc | **FAIL** | lệnh 2 — 5 lỗi có trước ở `scripts/crawl_wenku.py` | 1 | 2026-09-10T14:06:34Z | `dcf92c3` |

---

## 7. Danh sách NOT_RUN và feature tương ứng ĐANG DISABLED

| # | Mục NOT_RUN | Bằng chứng / lý do | Feature tương ứng | Trạng thái feature |
|---|---|---|---|---|
| 1 | Cloud provider thật (Gemini/Qwen/OpenRouter/Groq/NIM/Cerebras/Cloudflare/HF) | `quality.md` §1: `provider_profiles=0`, `cloud_processing_consents=0`, `budget_authorizations=0`, `rate_cards=0`, `usage_ledger=0` | `cloud_quality_mode` (manifest: `cloud_quality_live`) | **disabled** — API từ chối bật |
| 2 | Chất lượng bản dịch (corpus 30 đoạn, blind bilingual review, adequacy/fluency/consistency) | `quality.md` §2 V03.1–V03.6 NOT_RUN; `rights_evidence=0`, nguồn crawl `license=null` | `auto_approve_translation` (manifest: `auto_approve_translation_live`) + không gắn badge chất lượng nào | **disabled** |
| 3 | VieNeu TTS thật (probe/playback, cold/warm RTF, clipping) | `quality.md` §1–§2: không có `data/models`, `voice_presets=0`, `speech_segments=0`, không người nghe | `vieneu_tts_live` (không có flag API — feature chưa mở) | **disabled / không mở** |
| 4 | FFmpeg thật (master/part-master audio thật) | `preflight.ps1` FAIL §1.2: symlink `ffmpeg.exe` gãy, target không tồn tại; `perf-audio500.json` dùng fake processor | `ffmpeg_real_master` | **disabled** (preflight chặn khi thiếu FFmpeg) |
| 5 | Long-task khi gõ (C2K editor, browser thật) | `perf-editor8tab.json` → dòng "long task khi gõ" `status: NOT_RUN` (không có engine render/DOM trong harness offline) | Không có flag; tuyên bố chất lượng UI-performance bị chặn | **không được claim PASS** |
| 6 | Heap 30 phút + phiên SSE đủ 30 phút | `perf-sse30min.json` → dòng "heap phút 30 so với phút 5" `status: NOT_RUN`, phiên chỉ 90,1 s / 1.800 s | Không có flag; G-PERF giữ NOT_RUN | **không được claim PASS** |
| 7 | Corpus có quyền + reviewer/người nghe | `quality.md` §2 V03.3–V03.5, V03.8 | `quality_evaluation_corpus` | **disabled** |
| 8 | Vector index (X06) | Ngoài dependency Phase 1; chưa có retrieval benchmark | `vector_index` (manifest: `vector_index_live`) | **disabled** (và `unsafe`) |
| 9 | Public export bỏ qua cổng quyền | Vi phạm F03 nếu bật | `public_export_bypass` | **disabled** — evidence ghi `BLOCKED` |
| 10 | G-CORE/G-UX chạy lại trong R01 | R01 chỉ chạy vùng hẹp theo yêu cầu task | — | — |

---

## 8. Kết luận (chính xác theo evidence)

1. **Phạm vi phát hành là `fixture-verified`.** Migration clean install, upgrade từ fixture legacy `0016`, đọc lại dữ
   liệu cũ qua route thật, backup/restore/rollback đúng checksum và cơ chế chặn flag theo evidence **đã chạy thật và PASS**
   trên máy này, tại commit `dcf92c3`, với dữ liệu fixture tạm — **không phải** kiểm định production.
2. **Không được gọi đây là "đã kiểm định production".** G-LIVE chưa đạt: không provider thật, không model TTS, không
   corpus có quyền, không reviewer/người nghe (§7 mục 1–3, 7).
3. **G-PERF chưa đạt đủ:** 2 dòng còn `NOT_RUN` — long-task khi gõ và heap/phiên 30 phút. Đây là **NOT_RUN, không phải
   PASS**; không hạ ngưỡng và không suy diễn từ số đo rút ngắn.
4. **Hai FAIL còn lại, ghi rõ chứ không che:** (a) preflight nghiêm fail vì FFmpeg trên máy hỏng thật; (b) `ruff check scripts`
   fail vì 5 lỗi có trước trong `scripts/crawl_wenku.py` (ngoài phạm vi R01). Cả hai đã được khoanh vùng và không ảnh hưởng
   các gate migration/rollback ở trên.
5. **Phát hiện vận hành cần xử lý:** data root của repo (`data/studio.sqlite3`) đang ở revision **`0004`**, trong khi head là
   `0018` — tức 14 migration chưa áp dụng. `run-studio.bat` nay sẽ **từ chối khởi động** cho tới khi `scripts/migrate.ps1`
   chạy xong (hoặc phục hồi từ backup đã verify). R01 **không** tự migrate data root thật.
6. **Điều kiện nâng phạm vi lên live:** mỗi mục G-LIVE cần credential/consent/budget hợp lệ, ledger, invocation provider
   thật, output thật và review/playback tương ứng; sau đó cập nhật `docs/validation/release-manifest.json` sang `PASS` cho
   đúng feature — flag chỉ mở được khi bản ghi đó đổi, không phải bằng một lệnh gọi API.
