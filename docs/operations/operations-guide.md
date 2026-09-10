# Vận hành — Hướng dẫn theo tình huống

Bổ trợ cho `README.md` (walkthrough import→export) và `docs/validation/release-gate.md` (cổng phát hành).
Mọi lệnh ở đây chạy trên **Windows**, từ thư mục gốc repo.

> **Phạm vi:** tài liệu này mô tả Phase 1 ở mức **fixture-verified**. Các đường cloud/model thật
> **chưa được kiểm định** và đang bị tắt — xem `release-manifest.md`.

## 1. Credential & keyring

Secret **chỉ** nằm trong keyring của hệ điều hành do backend quản lý (C04/C06). Không có secret trong
localStorage, response, log, URL hay SQLite dạng plaintext.

**Nhập / thay credential** (chỉ request provisioning mới chứa secret, dạng transient):

```powershell
# qua UI: Settings → Providers → nhập key → Lưu
# qua API (ví dụ profile đã tạo):
$csrf = (Invoke-RestMethod http://127.0.0.1:8765/api/security/bootstrap).csrfToken
Invoke-RestMethod -Method Put -Uri http://127.0.0.1:8765/api/cloud-profiles/<profileId>/credential `
  -Headers @{ 'X-CSRF-Token' = $csrf; Origin = 'http://127.0.0.1:8765' } `
  -ContentType 'application/json' -Body '{"secret":"<key>"}'
```

**Rotate:** gọi lại cùng endpoint với secret mới. **Hệ quả bắt buộc:** revision cũ không dispatch được nữa —
quote/plan cũ trở nên vô hiệu (S01/S03).

**Xoá credential:** xoá key ⇒ `secretConfigured=false`; profile không biến mất, chỉ mất secret.

**Kiểm chứng không rò rỉ** (đã có E2E): `frontend/e2e/provider-credentials.spec.ts` — đọc toàn bộ
`localStorage`/`sessionStorage` ở 3 thời điểm và bắt mọi request của page.

**Nếu keyring lỗi:** hệ thống **fail-closed** — không có đường nào để chạy tiếp với secret không đọc được.

## 2. Cấu hình cloud: profile + consent + budget

Ba thứ **bắt buộc cùng lúc** trước khi dispatch cloud; thiếu bất kỳ thứ nào thì guard chặn (S03/C06):

| Thứ | Vì sao | Thiếu thì |
|---|---|---|
| `provider_profile_id` | Chọn adapter/model, đã enable | 400/409 `PROFILE_NOT_FOUND`/`PROFILE_DISABLED` |
| `cloud_consent_id` | Đồng ý gửi dữ liệu đi provider | Chặn trước khi gọi mạng |
| `budget_authorization_id` + quote | Trần chi phí đã được cấp | Chặn; **giá unknown không tự thành free** |

Ngoài ra **rights** của project phải cho phép scope tương ứng (`TRANSLATE_VI`, `CREATE_AUDIO`,
`PUBLIC_STREAM`). Rights và consent là **hai thứ khác nhau**: consent cho phép *gửi dữ liệu đi*,
không thay thế quyền *xuất bản*.

Quote có TTL và gắn revision: **đổi model/plan ⇒ quote cũ hết hiệu lực**. Quote hết hạn bị từ chối.

## 3. Resume & BILLING_UNKNOWN

**Nguyên tắc bất di bất dịch:** request đã gửi mà mất response thì job vào `BILLING_UNKNOWN`.
Hệ thống **KHÔNG tự gửi lại** và **KHÔNG tự fallback** sang model/provider khác — vì có thể bị tính phí hai lần.

Cách xử lý:

1. Mở `/jobs` — job `BILLING_UNKNOWN` hiển thị rõ, **không** có nút "Thử lại" tự động.
2. Đối chiếu thủ công với bảng điều khiển của provider (usage/billing) để biết request đó có tính phí hay không.
3. Nếu provider xác nhận **không** tính phí: tạo job mới là an toàn.
   Nếu provider xác nhận **có** tính phí: ghi nhận vào ledger trước khi chạy lại.

**Cancel:** client cancel chỉ dừng vòng lặp phía studio, **không** bảo đảm provider đã ngừng tính phí.
Cancel được xử lý ở ranh giới an toàn; đo được p95 **75,5 ms** từ `CANCEL_REQUESTED` tới `CANCELED`.

**Restart/lease:** worker dùng lease + heartbeat. Tiến trình chết giữa đường thì attempt hết hạn được thu hồi;
segment đã commit **không** bị nhân đôi (đã kiểm bằng crash test thật).

## 4. Backup / restore / retention / rollback

**Tạo backup** (Settings → Storage, hoặc API). Backup gồm: file DB qua SQLite Online Backup API + snapshot
artifact + manifest có checksum.

- **Không copy WAL riêng**: snapshot dùng Online Backup API nên dữ liệu chỉ nằm trong WAL **vẫn vào backup**
  (đã đo: 3 hàng WAL-only có mặt trong 25/25 bản; 0 file `*-wal` trong backup).
- **Incremental**: artifact không đổi được `os.link` từ snapshot trước (fallback copy).
  Manifest ghi `linked_artifact_count`/`copied_artifact_count`/`base_backup_id`.
  **File DB vẫn luôn là bản sao đầy đủ** — không có block-level incremental cho DB.
- **Tự chứa**: xoá bản base **không** làm hỏng bản sau (đã kiểm độc lập: xoá hoàn toàn base rồi `verify()` ⇒ ok).
- **Progress**: `GET /api/storage/backups/progress` trả tiến độ khi đang chạy, `204` khi rảnh.

**Retention:** đổi số bản giữ lại **chỉ tạo kế hoạch** (`applied: false`), **không tự xoá file**.
Phải xác nhận để thực thi.

**Restore:** `restore_copy` yêu cầu `confirmTarget` khớp **đúng** đường dẫn đích và chỉ phục hồi vào
data root **mới** (từ chối target trùng/overlap, verify backup trước khi ghi).

**Rollback sau migration lỗi:**

```powershell
# 1. Dừng studio. 2. Xác định trạng thái:
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/preflight.ps1 -VerifyDatabase -SkipFfmpeg
# 3. Nếu MIGRATION_INCOMPLETE: KHÔNG chạy lại migration. Restore từ backup đã verify vào data root mới,
#    rồi trỏ studio sang data root đó. Rollback bằng backup, KHÔNG trỏ binary cũ vào DB mới.
```

> **Cảnh báo SQLite (R01 phát hiện thật):** SQLite báo `assume non-transactional DDL`.
> Migration lỗi giữa đường rollback **hàng version** nhưng **để lại DDL** (bảng đã tạo vẫn còn) và chạy lại
> báo `already exists`. Vì vậy "không có DB nửa vời" đến từ **phát hiện** (so revision-vs-head)
> cộng **restore từ backup**, **không** đến từ transaction.

## 5. Export bundle & upload thủ công

Export tạo bundle **tự chứa** gồm: audio master, `transcript.srt`, `ban-dich.md`,
`metadata.json`, `provenance.json`, `THIRD_PARTY_LICENSES.txt`, `checksums.sha256`.

- `checksums.sha256` phủ **mọi file khác trong zip**; `GET /exports/status` xác minh lại từng file
  **bên trong chính zip đó** và trả `verified`/`mismatches`.
- **Private archive KHÔNG tự cấp quyền public.** Nút publication bị disable khi gate chặn.
- **Stale**: sửa quyền / duyệt lại master / đổi bản dịch ⇒ bundle cũ được đánh `stale` kèm lý do
  (`RIGHTS_CHANGED`, `MASTER_CHANGED`, `TRANSLATION_CHANGED`, `GATE_BLOCKED`).
  Không đọc được provenance ⇒ `UNKNOWN_*` và **vẫn tính là stale** (fail-closed).
- **Upload là thao tác THỦ CÔNG** (C08): studio **không bao giờ** tự upload. Tải zip rồi upload bằng tay lên app chính.

## 6. Troubleshooting

| Triệu chứng | Nguyên nhân | Xử lý |
|---|---|---|
| `PREFLIGHT FAIL: ffmpeg … could not be executed` | FFmpeg có trên PATH nhưng không chạy được (ví dụ symlink gãy) | Cài FFmpeg hoạt động, **hoặc** chạy `preflight.ps1 -SkipFfmpeg`. Launcher `run-studio.bat` đã tự dùng `-SkipFfmpeg` vì FFmpeg bị tắt trong release manifest. Render audio thật vẫn cần FFmpeg |
| `PREFLIGHT FAIL: the database is at 0004 but head is 0018` | Data root cũ hơn schema | `scripts/migrate.ps1` — **backup trước** |
| `state MIGRATION_INCOMPLETE` | Migration lỗi giữa đường (SQLite để lại DDL) | **Không** chạy lại. Restore từ backup đã verify |
| Job ở `BILLING_UNKNOWN` | Mất response sau khi đã gửi | Đối chiếu provider thủ công; **không** tự resend |
| Nút publication bị disable | Gate quyền chặn | Xem lý do trong panel; bổ sung rights grant + evidence |
| Export báo `SRT_REQUIRED` | Chương chưa có SRT READY | Render audio trước (master + SRT sinh cùng lượt) |
| `422 INVALID_REQUEST` khi lưu từ UI | (đã sửa ở commit `8611203`) Content-Type thiếu cho body đã stringify | Cập nhật frontend lên commit mới |

## 7. Liên quan

- `README.md` — walkthrough import→export.
- `docs/validation/release-gate.md` — cổng phát hành + bằng chứng.
- `docs/validation/release-manifest.md` — feature nào đang bật/tắt và vì sao.
- `docs/validation/phase1.md` — trạng thái từng task theo ba mức.
- `docs/plans/migration-plan.md` — kế hoạch migration + rollback.
