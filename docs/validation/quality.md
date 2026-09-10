# V03 — Đánh giá dịch và TTS bằng evidence thật

Trạng thái: **NOT_RUN / BLOCKED toàn bộ** theo đúng quy tắc plan §2 (task V03 chưa thể chạy vì thiếu môi trường live).
Tài liệu này ghi lại **bằng chứng kiểm tra môi trường** thay vì suy đoán, liệt kê từng mục của V03 kèm trạng thái,
và mô tả chính xác thủ tục phải chạy khi môi trường tồn tại. Không mục nào ở đây được gắn badge chất lượng.

- Thời điểm kiểm tra: **2026-09-10T07:55:01Z**
- Commit: `4e94fdd148e78a3f6db38d051004b918c9c3c3db` (nhánh `codex/implement-upgrade-plan`)
- Môi trường: Windows 11 (10.0.26200), Python 3.12.13, Node v24.11.1; data root `data/`
- Nguyên tắc: **không gọi cloud trả phí, không tải model, không tự phát sinh spend** chỉ để đóng checklist.

## 1. Bằng chứng kiểm tra môi trường (chạy thật, không suy luận)

Probe đọc trực tiếp SQLite của studio và cây `data/`:

| Kiểm tra | Kết quả | Ý nghĩa cho V03 |
|---|---|---|
| `data/models` tồn tại | **Không** | Không có model TTS cục bộ |
| `data/models/voices/voice-presets.manifest.json` | **Không có** | Catalog giọng rơi về mặc định và mọi preset đều `available=false` |
| `vieneu-vi-int8.onnx` | **Không có file** | Không thể tổng hợp giọng VieNeu thật |
| `vais1000.onnx` (Piper) | **Không có file** | Không có TTS cục bộ thay thế |
| `voice_presets` | **0 hàng** | Chưa có preset nào được verify trong DB |
| `voice_plans` / `speech_segments` | **0 / 0** | Chưa từng render audio trong data root này |
| `provider_profiles` | **0 hàng** | Không có credential cloud nào được cấu hình (đúng C06: chỉ backend keyring) |
| `cloud_processing_consents` | **0 hàng** | Không có consent cloud đang hiệu lực |
| `budget_authorizations` | **0 hàng** | Không có authorization ngân sách ⇒ guard S03 chặn dispatch cloud |
| `rate_cards` | **0 hàng** | Giá chưa xác định ⇒ theo S03, giá unknown bị chặn |
| `usage_ledger` | **0 hàng** | Chưa có invocation provider nào được ghi ledger |
| `rights_evidence` | **0 hàng** | Không có bằng chứng quyền cho corpus chất lượng |
| `rights_grants` | 8 hàng (chỉ trên project tự tác giả test) | Không phải corpus 3 thể loại có quyền |
| `data/crawled_novels/*/metadata.json` | `source`/`source_url`/`license` đều **null** | Nguồn crawl không có provenance quyền ⇒ **không** dùng làm corpus đánh giá |
| `data/smoke-reports/*.json` | 2 file, `status = ready_for_local_tts_manual_call`, `cloud_consent_id_present=false`, `authorization_id_present=false` | Đây là **preflight**, không phải invocation; chưa từng có live smoke thật |

Kết luận môi trường: **không có model TTS, không có credential/consent/budget cloud, không có corpus có quyền,
và không có người review/người nghe**. Cả ba nhóm đầu vào của V03 đều thiếu, nên mọi mục dưới đây là NOT_RUN.

## 2. Trạng thái từng mục của V03

| # | Hạng mục theo plan | Trạng thái | Blocker cụ thể |
|---|---|---|---|
| V03.1 | Corpus có quyền: 30 đoạn | **NOT_RUN** | `rights_evidence = 0`; crawl source có `license=null` |
| V03.2 | 3 thể loại × 3 chương liên tiếp | **NOT_RUN** | Không có corpus được cấp quyền; 3 project hiện có đều là project test tự tác giả |
| V03.3 | Blind bilingual review (người chấm) | **NOT_RUN** | Không có reviewer; không thể thay bằng model tự chấm (sẽ là claim vượt evidence) |
| V03.4 | Adequacy / fluency / style / consistency | **NOT_RUN** | Phụ thuộc V03.3 |
| V03.5 | Mất nghĩa / mất tên / mất số | **NOT_RUN** | Phụ thuộc V03.3 |
| V03.6 | Correction time, cost | **NOT_RUN** | `usage_ledger = 0`, `rate_cards = 0` ⇒ không có cost thật để báo |
| V03.7 | Cold/warm TTS (RTF, RSS, duration) | **NOT_RUN** | Không có model VieNeu; `speech_segments = 0` |
| V03.8 | Nghe sample/master, clipping, khoảng lặng | **NOT_RUN** | Không có audio thật và không có người nghe |
| V03.9 | Authorized cloud smoke có ledger + output | **NOT_RUN / BLOCKED** | `provider_profiles = 0`, `cloud_processing_consents = 0`, `budget_authorizations = 0` |
| V03.10 | Report model/prompt/context/voice hashes | **NOT_RUN** | Chưa có lần chạy nào sinh hash để báo |

**Critical unresolved trong sample được approve:** không áp dụng — không có sample nào được approve, nên không có
cơ sở để tuyên bố "0 critical". Mục này giữ NOT_RUN, **không** được tính là PASS.

## 3. Hệ quả bắt buộc (đang áp dụng)

- Mọi feature chất lượng **giữ disabled**: không bật cloud provider nào, không gắn nhãn chất lượng cho model nào,
  không đưa badge benchmark nào lên UI (khớp P02: benchmark badge tách khỏi curated recommendation).
- Không có tuyên bố "live verified" hoặc "quality verified" ở bất kỳ tài liệu nào của repo.
- Đường fake/offline **đã** chạy thật (browser E2E, fixture) chỉ chứng minh **plumbing**, không chứng minh chất lượng —
  xem `docs/validation/progress-upgrade-plan.md`.

## 4. Thủ tục phải chạy khi môi trường đã sẵn sàng

Điều kiện tiên quyết (mỗi mục phải có evidence tương ứng, theo G-LIVE ở plan §7):

1. **TTS cục bộ**: cài model VieNeu + license snapshot, đặt manifest tại
   `data/models/voices/voice-presets.manifest.json` (schema `truyenaudio-studio.voice-presets.v1`), rồi xác nhận
   `GET /api/voices` trả `available=true` cho preset đã cài.
2. **Corpus có quyền**: tạo project với `rights_status=CLEARED` + `rights_evidence` thật (hợp đồng/giấy phép),
   import 3 thể loại × 3 chương liên tiếp và 30 đoạn chấm điểm.
3. **Cloud (nếu muốn mục V03.9)**: provisioning credential qua keyring, tạo consent và budget authorization hợp lệ,
   xác nhận `rate_cards` có giá cho model dùng — nếu không, guard phải chặn (đó là hành vi đúng).
4. **Người review**: tối thiểu 1 người chấm blind bilingual + 1 người nghe; ghi rõ số người, không suy ra tỷ lệ đại diện.

Sau đó chạy:

- Đo hiệu năng TTS bằng harness offline trước (`scripts/benchmarks/run.py --fixture AUDIO500`) để tách
  chi phí plumbing khỏi chi phí model, rồi mới đo cold/warm có model thật.
- Authorized cloud smoke qua script smoke với ledger + output thật, ghi `usage_ledger` và hash model/prompt/context.
- Ghi kết quả vào chính tài liệu này, mỗi mục PASS/FAIL/NOT_RUN kèm command, exit code, timestamp, commit,
  hash fixture, máy và artifact evidence (plan §7).

## 5. Kết luận

V03 **không đạt** và không thể đạt trong môi trường hiện tại; đây là blocker môi trường, không phải lỗi triển khai.
Theo G-RELEASE, Phase 1 chỉ có thể phát hành ở phạm vi **fixture-verified** với các feature live chưa chứng minh
**disabled** và danh sách giới hạn ghi rõ — không được gọi là đã kiểm định production.
