# Migration Plan (LOCKED SCOPE; Q01 C)

> Cập nhật bởi **R01** (2026-09-10, commit `dcf92c3`) theo thực tế repo. Bảng gốc giữ nguyên phạm vi đã khoá;
> cột "Thực tế" ghi điều đã chạy và điều còn NOT_RUN. Báo cáo cổng phát hành: [release-gate.md](../validation/release-gate.md).

## 1. Hiện trạng schema

| Mục | Giá trị |
|---|---|
| Chuỗi migration | `0001` … `0018` — **18** revision, tuyến tính, head **`0018`** |
| Hai migration mới nhất | `0017_voice_preview_jobs` (bảng `voice_preview_jobs` + index) và `0018_job_kind_summarize` (rebuild `jobs` để mở rộng whitelist `ck_jobs_kind_enum` thêm `SUMMARIZE`) — **cả hai ADDITIVE** |
| Kiểm tra tự động | `scripts/preflight.ps1` đọc thẳng chuỗi alembic (`app/db/migration_status.py`) nên số migration **không** bị hard-code; `-VerifyDatabase` bắt buộc DB đúng head |
| Data root repo | `data/studio.sqlite3` hiện ở revision **`0004`** (chưa áp dụng 14 migration) — R01 **không** tự migrate data root thật; `run-studio.bat` sẽ chặn khởi động cho tới khi chạy `scripts/migrate.ps1` |

## 2. Từng bước (giữ nguyên phạm vi đã khoá, thêm cột thực tế)

| Bước | Current | Action | Guard/rollback | Thực tế sau R01 |
|---|---|---|---|---|
| 0 | SQLite + artifacts hiện hữu | Backup SQLite bằng API, checksum artifacts, snapshot tests | Không chạy trên data duy nhất; restore drill | **Đã diễn tập:** backup ở `0016` → upgrade `head` → restore sang data root mới; sha256/byte_size khớp manifest, `integrity=ok`, digest nội dung + đếm hàng trùng khít, artifact đúng sha256. Xem `backend/tests/storage/test_backup_rollback_drill.py` |
| 1 | Gemini/Qwen route riêng, key issues | CredentialStore + provider registry + typed error; giữ route compatibility tạm | Feature flag; reject invalid profiles, không xóa secrets cũ tự động | **Flag đã fail-closed theo evidence:** `docs/validation/release-manifest.json` + `app/modules/settings/feature_flags.py`; `cloud_quality_live` = `NOT_RUN` ⇒ `cloud_quality_mode` **không bật được** qua API |
| 2 | Translation run đồng bộ | ExecutionPlan + segment jobs/attempts/context snapshot | Legacy run read-only; idempotency và expected hash | Schema/route đã có (F03, J01). **Không có route legacy cần giữ:** grep trong `backend/app` chỉ thấy "legacy keyring reader" (không liên quan đọc dữ liệu); route hiện tại đọc được dữ liệu tạo ở `0016` (4 route trả 200, xem release-gate §4) |
| 3 | Worker handler unavailable | Wire handlers từng JobKind, recovery/cancel | Canary fake provider trước live | TRANSLATE/SYNTHESIZE/REVIEW/SUMMARIZE đã nối; **handler cloud thật vẫn NOT_RUN** (thiếu credential/consent/budget) |
| 4 | Inline UI/first voice/hash-only audio | Rewrite UI workspace, player, settings; backend routes giữ contract | Legacy route fallback tới khi E2E pass | UI/E2E thuộc U01–U10/V02; **G-UX không được chạy lại trong R01** |
| 5 | VieNeu bridge giả CLI/whole chapter | Pin manifest, segment synthesis, probe/master | Giữ master cũ, artifact atomic | Code xong (A01–A04); **VieNeu thật + FFmpeg thật NOT_RUN** (không model; symlink FFmpeg trên máy hỏng) ⇒ feature giữ disabled |
| 6 | Events/diagnostics hiện tại | Sequence transition + actual usage/model + redaction | Schema additive, replay fixture | Migration `0004`–`0018` additive; diễn tập upgrade chứng minh dữ liệu cũ còn nguyên và bảng mới rỗng |
| 7 | Cleanup | Xóa dead route/demo hardcode chỉ sau usage scan và E2E | Không xóa migration/data trước backup | **Chưa làm** (thuộc R03) — R01 không xóa gì |

Mỗi bước có migration script idempotent, dry-run/report, checksum và kiểm thử rollback. Không chuyển cloud provider/model hoặc thay schema trong cùng một release nếu chưa có fixture/backup.

## 3. Quy tắc rollback (đã kiểm chứng bằng diễn tập)

1. **Trước mọi upgrade:** tạo backup qua API/`BackupService` và **verify** (sha256 + `integrity_check` + con trỏ artifact).
2. **Upgrade:** `scripts/migrate.ps1` — in revision trước/sau và **fail** nếu DB không đạt head.
3. **Khởi động:** `run-studio.bat` chạy `preflight.ps1 -VerifyDatabase` sau migrate; DB lệch head ⇒ **không** khởi động.
4. **Khi migration lỗi giữa đường:** SQLite báo `Will assume non-transactional DDL`. Version row rollback, nhưng **DDL đã tạo
   có thể còn lại** (diễn tập: bảng `rehearsal_half_state` sót lại, chạy lại ⇒ `already exists`). Vì vậy:
   **restore từ backup đã verify** hoặc viết migration sửa tiến về trước — **không** chạy lại migration hỏng,
   **không** trỏ binary cũ vào DB đã nâng cấp.
5. **Phát hiện:** `app/db/migration_status.py` phân loại `OK` / `DATABASE_MISSING` / `REVISION_MISSING` / `MIGRATION_INCOMPLETE`;
   `preflight.ps1` in ra và (khi `-VerifyDatabase`) fail với thông báo cụ thể.

## 4. Lệnh diễn tập (đã chạy, exit code thật)

```powershell
Set-Location D:\truyenaudio-studio
# 1. Clean install trên data root tạm trống
$env:STUDIO_DATA_ROOT = Join-Path $env:TEMP ("r01-clean-" + [guid]::NewGuid().ToString("N"))
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/migrate.ps1            # exit 0 -> revision=0018 status=OK
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/preflight.ps1 -VerifyDatabase -SkipFfmpeg  # exit 0

# 2..4. Upgrade legacy 0016 / fail migration / backup-rollback + feature flag
.venv\Scripts\python.exe -m pytest backend/tests/db backend/tests/storage -q      # exit 0 -> 59 passed
```

Chi tiết bằng chứng (timestamp, commit, số hàng trước/sau, phát hiện non-transactional DDL): [release-gate.md](../validation/release-gate.md).

## 5. Giới hạn còn lại (NOT_RUN, không tính PASS)

- Cloud provider thật, chất lượng dịch (corpus/reviewer), VieNeu thật, FFmpeg thật, long-task browser, heap 30 phút: **NOT_RUN**
  — xem [quality.md](../validation/quality.md) và `docs/validation/perf-*.json`; feature tương ứng giữ **disabled** trong
  `docs/validation/release-manifest.json`.
- R01 không chạy lại G-CORE/G-UX (full suite + Playwright) — bằng chứng trước đó ở
  [progress-upgrade-plan.md](../validation/progress-upgrade-plan.md).

Theo [plan v2](implementation-plan.md): F01 tạo backup/restore; F03 và migration theo task thêm schema additive; R01 diễn tập clean install/upgrade/rollback trên bản sao; R02 bàn giao Phase 1; R03 chỉ dọn code ở release sau có usage evidence. Rollback schema mới bằng bộ backup DB+artifacts đã kiểm chứng, không cho binary cũ ghi vào DB đã nâng cấp. Chưa xóa wrapper hoặc artifact cũ khi chỉ fixture pass.

X01–X07 là Phase 2, không chặn Phase 1. Từng cloud/TTS/quality feature chưa có live evidence giữ disabled/NOT_RUN; không đồng nhất code đã viết với provider đã kiểm chứng. Cấu hình Ollama/LM Studio/local LLM vẫn trì hoãn theo Q03 C.
