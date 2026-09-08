# Migration Plan (LOCKED SCOPE; Q01 C)

| Bước | Current | Action | Guard/rollback |
|---|---|---|---|
| 0 | SQLite + artifacts hiện hữu | Backup SQLite bằng API, checksum artifacts, snapshot tests | Không chạy trên data duy nhất; restore drill |
| 1 | Gemini/Qwen route riêng, key issues | CredentialStore + provider registry + typed error; giữ route compatibility tạm | Feature flag; reject invalid profiles, không xóa secrets cũ tự động |
| 2 | Translation run đồng bộ | ExecutionPlan + segment jobs/attempts/context snapshot | Legacy run read-only; idempotency và expected hash |
| 3 | Worker handler unavailable | Wire handlers từng JobKind, recovery/cancel | Canary fake provider trước live |
| 4 | Inline UI/first voice/hash-only audio | Rewrite UI workspace, player, settings; backend routes giữ contract | Legacy route fallback tới khi E2E pass |
| 5 | VieNeu bridge giả CLI/whole chapter | Pin manifest, segment synthesis, probe/master | Giữ master cũ, artifact atomic |
| 6 | Events/diagnostics hiện tại | Sequence transition + actual usage/model + redaction | Schema additive, replay fixture |
| 7 | Cleanup | Xóa dead route/demo hardcode chỉ sau usage scan và E2E | Không xóa migration/data trước backup |

Mỗi bước có migration script idempotent, dry-run/report, checksum và kiểm thử rollback. Không chuyển cloud provider/model hoặc thay schema trong cùng một release nếu chưa có fixture/backup.

Theo [plan v2](implementation-plan.md): F01 tạo backup/restore; F03 và migration theo task thêm schema additive; R01 diễn tập clean install/upgrade/rollback trên bản sao; R02 bàn giao Phase 1; R03 chỉ dọn code ở release sau có usage evidence. Rollback schema mới bằng bộ backup DB+artifacts đã kiểm chứng, không cho binary cũ ghi vào DB đã nâng cấp. Chưa xóa wrapper hoặc artifact cũ khi chỉ fixture pass.

X01–X07 là Phase 2, không chặn Phase 1. Từng cloud/TTS/quality feature chưa có live evidence giữ disabled/NOT_RUN; không đồng nhất code đã viết với provider đã kiểm chứng. Cấu hình Ollama/LM Studio/local LLM vẫn trì hoãn theo Q03 C.
