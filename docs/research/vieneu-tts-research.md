# Nghiên cứu VieNeu-TTS và audio

Ngày: 07/09/2026. Không cài model, không chạy inference và chưa nghe audio trên máy này.

## Discovery source hiện tại

`backend/app/providers/vieneu.py` hiện giả định một file model/CLI với `--model`, `--sample-rate`, `--output`, chỉ catalog preset `vieneu-vi-int8`; route voice lấy preset đầu tiên có sẵn. `modules/voices/catalog.py` có catalog/preview view nhưng preview chỉ trả job view; `modules/speech/workflow.py` là orchestration lớn, còn `worker.py:192-198` đăng ký handler chưa cấu hình. Route audio chính chỉ nhận master hash và approve, không nối AudioReview/player. Đây là evidence source, chưa là kết quả runtime.

## VieNeu version và capability cần xác minh

POC pin trong `backend/pyproject.toml` là `vieneu==3.2.9`. Đọc metadata package trong phiên cho thấy wheel 3.2.9 dùng API Python `infer`/`list_preset_voices`, mặc định v3turbo CPU int8, native 48 kHz và nhiều preset; console scripts là `vieneu-web`/`vieneu-stream`, không xác nhận CLI synth như adapter đang giả định. Latest PyPI metadata được thấy là 3.6.4. Docs/model card legacy có thể mô tả GGUF khác v3.3+, nên không dùng bảng RAM cũ để cam kết.

| Khả năng | Kết luận hiện tại | Hành động sau quyết định |
|---|---|---|
| Voice list/preset | Có API catalog trong package; app chỉ expose một preset | Tạo manifest pinned gồm voice id, locale, model revision, license, sample artifact |
| Sample/preview | UX component có nhưng production preview chưa durable | Preview job + artifact + cache key + player; không autoplay |
| Speed/pitch | Không thấy tham số native hữu hiệu trong POC; playback/FFmpeg có thể là hậu xử lý | Chỉ hiển thị control khi capability probe xác nhận; ghi rõ playback vs synthesis |
| Style/emotion | Adapter hiện bỏ qua style; không hứa hẹn engine hỗ trợ | Ẩn control hoặc đánh dấu unsupported |
| Chunking | Source có segment/speech objects; render API whole chapter và commit cuối | Segment-level resumable jobs, giữ audio cũ tới khi bản mới ready |
| Sample rate | Engine native 48 kHz; master app mặc định 44.1 kHz | Resample thật ở boundary master, lưu source/output sample rate |
| RAM/latency | Chưa benchmark trên PC 8 GB | Đo cold/warm, 1 segment/500 segment, đồng thời với translator; giới hạn concurrency 1 |
| License | Cần snapshot model/package license | Chặn preview/render khi manifest thiếu license evidence |

## Audio architecture đề xuất

`ApprovedTranslationRevision → normalize narration → split speech segments → preview/synthesize job → immutable TTS segment artifact → probe → merge/master FFmpeg → SRT/metadata → audio review → export gate`.

Cache key gồm translation revision hash, normalized text, engine/model/voice revision, locale, các parameter thực sự ảnh hưởng synthesis, sample rate và postprocess version. Preview cache khác chapter cache. Job idempotency là `(chapter, translation_revision, segment, voice_plan, settings_hash)`; retry chỉ segment failed. Worker ghi progress/heartbeat/checkpoint; cancel là safe point. Audio artifact chỉ active sau probe và checksum; master mới không xóa master cũ.

## Voice Picker acceptance đề xuất

Row có voice name, locale, metadata có nguồn, readiness, nghe sample, selected và playing độc lập. Sample text có giới hạn/đếm ký tự. A/B dùng cùng text/parameter. Một player tại một thời điểm, keyboard play/pause/seek, không autoplay. Chapter mặc định một narrator; multi-voice là opt-in và phải review role mapping.

## Verification protocol

Trên máy thật, chạy fixture tiếng Việt có thoại, số, tên và dấu câu; đo time-to-first-audio, duration, RMS/LUFS/peak, drop/clipping, memory peak và cold/warm. Nghe mù ít nhất 5 đoạn; kiểm tra pronunciation issues bằng ASR advisory nếu bật. Xác nhận model/license manifest và exact API trước khi đổi adapter. Paid/cloud smoke vẫn `NOT_RUN` cho tới khi có consent, budget, usage ledger và output audio thật.

## Nguồn và giới hạn

Package metadata/PyPI và repository/model card VieNeu phải được snapshot cùng version khi implementation bắt đầu; docs legacy không đủ làm capability proof. Không coi tên voice, model size hay sample rate là bằng chứng chất lượng kể chuyện. Chi tiết line evidence current app nằm trong [current-system-analysis.md](current-system-analysis.md).
