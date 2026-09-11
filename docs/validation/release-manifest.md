# Release manifest — bản người đọc

Bản máy đọc: `docs/validation/release-manifest.json` (schema `truyenaudio-studio.release.v1`, sinh bởi R01).
Tài liệu này giải thích **từng feature**, **nghĩa của trạng thái evidence**, **feature có đang bật không**, và **cách tự kiểm chứng**.

**Phạm vi phát hành: `fixture-verified`.** Không phải báo cáo kiểm định production.

## Quy tắc bắt buộc

> **Chỉ evidence `PASS` mới được bật flag.**
> NOT_RUN / FAIL / BLOCKED / thiếu / JSON hỏng ⇒ flag **không bật được**; API trả
> `400 FEATURE_FLAG_EVIDENCE_NOT_PASS:<flag>`.
> Đây là cơ chế **fail-closed** trong `backend/app/modules/settings/release_manifest.py` + `feature_flags.py`.
> Flag `unsafe` vẫn bị chặn **kể cả khi** evidence là PASS.

## Bảng feature

Cột **Đang bật?**: `—` = feature **không có flag runtime** (luôn chạy theo evidence);
`❌` = có flag và **đang tắt**.

| Feature | Evidence | Đang bật? | Nghĩa & cách kiểm chứng |
|---|---|---|---|
| `clean_install_migration` | **PASS** | — | Data root trống → `alembic upgrade head` đạt revision 0019, 19 migration, 38 bảng, integrity ok. Kiểm: `pytest backend/tests/db/test_migration_rehearsal.py -q` |
| `legacy_upgrade_preserves_data` | **PASS** | — | DB ở 0016 có dữ liệu thật → upgrade lên 0019, số hàng trước == sau. Kiểm: cùng file test trên |
| `failed_migration_detection` | **PASS** | — | Migration lỗi giữa đường được báo `MIGRATION_INCOMPLETE`; revision-vs-head phát hiện được. Kiểm: `scripts/preflight.ps1 -VerifyDatabase` |
| `backup_rollback_checksum` | **PASS** | — | Backup trước upgrade → restore vào data root mới: sha256/byte_size/integrity khớp + digest nội dung logic y hệt. Kiểm: `pytest backend/tests/storage/test_backup_rollback_drill.py -q` |
| `legacy_read_routes` | **PASS** | — | 4 route đọc thật trả 200 trên dữ liệu tạo từ schema 0016 |
| `feature_flag_evidence_gate` | **PASS** | — | Flag chỉ bật khi manifest ghi PASS; unsafe luôn bị chặn. Kiểm: `pytest backend/tests/settings -q` |
| `g_perf_offline_fixtures` | **PASS** | — | 14/14 report có `overallStatus=PASS` — **không** có nghĩa G-PERF xanh hoàn toàn: tổng 34 dòng ngưỡng = 32 PASS · 0 FAIL · **2 NOT_RUN** (2 dòng ngay dưới). Kiểm: `scripts/benchmarks/run.py --fixture S1` |
| `g_perf_long_task_typing` | **NOT_RUN** | ❌ | Cần browser thật (PerformanceObserver + IME); harness chỉ có Python/Node headless | 
| `g_perf_sse_heap_30min` | **NOT_RUN** | ❌ | Phiên đo rút ngắn còn 90,1 s, không đủ 1.800 s |
| `cloud_quality_live` | **NOT_RUN** | ❌ flag `cloud_quality_mode` | Chưa có credential/consent/budget cloud ⇒ chưa gọi provider thật lần nào |
| `vieneu_tts_live` | **NOT_RUN** | ❌ | `data/models` **không tồn tại**, không có model + license |
| `quality_evaluation_corpus` | **NOT_RUN** | ❌ | Không có corpus được cấp quyền, không có reviewer — xem `quality.md` |
| `ffmpeg_real_master` | **NOT_RUN** | ❌ | FFmpeg không chạy được: symlink WinGet gãy (target không tồn tại) |
| `vector_index_live` | **NOT_RUN** | ❌ flag `vector_index` | X06 code-complete + 34 contract test PASS offline; vẫn NOT_RUN — chưa có embedding artifact thật/benchmark relevance/latency/RSS |
| `auto_approve_translation_live` | **NOT_RUN** | ❌ flag `auto_approve_translation` | Chưa có evidence chất lượng để tự duyệt |
| `public_export_bypass_live` | **BLOCKED** | ❌ flag `public_export_bypass` | Bị chặn **có chủ đích**: C08 yêu cầu kiểm quyền public trước khi xuất |
| `provider_groq_live` | **NOT_RUN** | ❌ flag `provider_groq_live` | X01: 49 contract test PASS offline; chưa có account/key, chưa review terms ⇒ live NOT_RUN |
| `provider_nim_live` | **NOT_RUN** | ❌ flag `provider_nim_live` | X02: 66 contract test PASS offline; chưa có NVIDIA account/license, chưa chạy endpoint thật ⇒ live NOT_RUN |
| `provider_cerebras_live` | **NOT_RUN** | ❌ flag `provider_cerebras_live` | X03: 45 contract test PASS offline; chưa có key, quota free chưa xác nhận ⇒ live NOT_RUN |
| `provider_cloudflare_live` | **NOT_RUN** | ❌ flag `provider_cloudflare_live` | X04: 53 contract test PASS offline; chưa có account/token, region chưa xác nhận ⇒ live NOT_RUN |
| `provider_huggingface_live` | **NOT_RUN** | ❌ flag `provider_huggingface_live` | X05: 67 contract test PASS offline; chưa có token, license model/routing chưa review ⇒ live NOT_RUN |

## Tự kiểm chứng toàn bộ

```powershell
# 1. Bảng máy đọc
Get-Content docs/validation/release-manifest.json -Raw | ConvertFrom-Json |
  Select-Object -ExpandProperty features | Format-Table id, evidence, flag

# 2. Chạy gate
.venv\Scripts\python.exe -m pytest backend/tests/db backend/tests/storage backend/tests/settings -q

# 3. Kiểm trạng thái schema của một data root
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/preflight.ps1 -VerifyDatabase -SkipFfmpeg
```

## Liên quan

- `docs/validation/release-gate.md` — báo cáo G-RELEASE đầy đủ (command + exit code + timestamp).
- `docs/validation/phase1.md` — ma trận task theo ba mức code complete / fixture verified / live verified.
- `docs/validation/quality.md` — vì sao V03 NOT_RUN/BLOCKED (kèm bằng chứng probe môi trường).
