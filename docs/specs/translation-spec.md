# Translation Spec (LOCKED BASELINE; Q04 C)

## Default profile

Balanced mặc định: translate theo segment/chunk có glossary + approved context → deterministic QA; không tự gọi review/polish. Economy/Quality/Maximum là lựa chọn rõ theo chapter/batch; Quality/Maximum chỉ opt-in, quote từng stage, hard budget và human approval theo [C06](implementation-contracts.md). Không tự chuyển profile vì một stage lỗi.

Schema style, TM, nhân vật/quan hệ, summary, snapshot và API revision được chốt tại C01–C05 của [contract bổ sung](implementation-contracts.md). Plan task C01–C06/J01–J04/U06–U09 triển khai và kiểm chứng từng phần.

## Context contract

Snapshot source revision, style/prompt, glossary, character/addressing, approved summaries, exact TM matches và token budget. Không truncate source; overflow thì rút context/chia tại biên nghĩa. Lưu trace mục đã dùng/cắt.

## Quality gates

Segment IDs đủ/đúng thứ tự; không mất số/đơn vị/tên; locked glossary đúng; residual Han/meta/repetition được đánh dấu; critical không thể force approve nếu policy chưa cho phép. Human approval là điều kiện audio/export.

## Revision

Output immutable; sửa tạo revision; expected hash chống ghi đè; upstream change đánh dấu translation/audio/export stale và hiển thị phạm vi.
