# Nghiên cứu model và cơ chế khám phá

Ngày: 07/09/2026. Đây là catalog design, không phải danh sách model đã được tài khoản hiện tại kiểm chứng.

## Kết luận

Chọn hybrid discovery: curated models có version và nguồn/ngày để người dùng bắt đầu nhanh; dynamic `/models` hoặc catalog native bổ sung model; capability và giá phải được normalize theo model, region, account tier và thời điểm. Không gắn `Free`, `Fast`, `High Quality`, `Translation Optimized` nếu chưa có metadata hoặc benchmark provenance.

| Lựa chọn | Ưu điểm | Nhược điểm | Đánh giá |
|---|---|---|---|
| A — Curated hard-code | UX ổn định, dễ QA, ít request | Drift, thiếu model mới, phải cập nhật | Dùng cho recommended seed |
| B — Dynamic hoàn toàn | Bao phủ catalog, theo provider | Metadata lỗi/không đồng nhất; khó bảo đảm model phù hợp | Không đủ làm UX mặc định |
| C — Hybrid | Khởi đầu rõ, vẫn khám phá được model mới | Cần provenance, cache và trạng thái stale | Khuyến nghị |

## Model metadata contract đề xuất

Mỗi snapshot gồm `provider`, `model_id`, `display_name`, `source_url`, `retrieved_at`, `availability` (`unknown/available/unavailable`), `context_input`, `max_output`, `source_languages`, `target_languages`, `streaming`, `structured_output`, `native_translation`, `pricing_input`, `pricing_output`, `pricing_unit`, `free_policy`, `region`, `license_url`, `benchmark_status`, `benchmark_run_id`, `deprecation_at`. `null` nghĩa là chưa biết; không chuyển thành 0 hoặc false.

Discovery trả về `DISCOVERY_OK`, `DISCOVERY_AUTH_REQUIRED`, `DISCOVERY_RATE_LIMITED`, `DISCOVERY_UNAVAILABLE`, `DISCOVERY_STALE`. Chỉ `DISCOVERY_OK` được phép cập nhật snapshot; lỗi phải giữ snapshot cũ kèm tuổi dữ liệu. Refresh có ETag/TTL và không chạy trong request dịch.

Model picker mặc định lọc provider đã cấu hình, capability phù hợp ngôn ngữ, context đủ cho chunk, policy cloud và budget. Bộ lọc Free/Paid/Recommended/Fast/High Quality/Long Context/Translation Optimized/Local/Cloud là các thuộc tính độc lập; nhãn benchmark phải mở được dataset, ngày và version. Provider capability có thể là `native`, `emulated`, `unsupported`, `unknown` để tránh boolean quá mạnh.

## Candidate shortlist từ provider research

- Bulk candidate: Qwen-MT sau khi sửa wire contract và đo ZH→VI; Gemini giữ làm candidate cloud hiện có.
- Cloud router candidate: OpenRouter để kiểm tra shared OpenAI transport, nhưng actual downstream model phải lưu trong attempt.
- Local candidate: chọn **một** Ollama hoặc LM Studio sau spike RAM/quality trên máy 8 GB; model weights/license được snapshot riêng.
- Groq/Cerebras/NIM/Cloudflare/HF là phase 2/optional, không làm baseline trước khi P0 credential/guard/ledger đúng.

## Benchmark protocol

Dùng corpus bilingual có quyền xử lý: 30 đoạn và 3 chuỗi × 3 chương; chấm nghĩa 35%, không thêm/bớt 20%, tên/thuật ngữ 20%, xưng hô 15%, văn phong/format 10%; critical omission/number/name là hard gate. Mỗi cặp chạy lặp lại, lưu model snapshot, prompt/context hashes, usage, latency median/p95, retry/fallback và thời gian sửa của người duyệt. Kết quả chưa có trong phiên này; toàn bộ quality labels hiện là `UNMEASURED`.

## Nguồn chính thức

Ma trận API, quota, context, pricing và nguồn được ghi tại [provider-research.md](provider-research.md), gồm tài liệu NVIDIA, OpenRouter, Google, Groq, Cerebras, Cloudflare, Hugging Face, Ollama, LM Studio và Alibaba được truy cập 07/09/2026. OpenAI-style compatibility không đồng nghĩa capability, giá hay chất lượng giống nhau.
