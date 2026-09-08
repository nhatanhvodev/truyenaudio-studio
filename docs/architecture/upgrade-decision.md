# Điểm quyết định nâng cấp

Ngày: 07/09/2026. Trạng thái: **ĐÃ KHÓA THEO QUYẾT ĐỊNH NGƯỜI DÙNG**.

Quyết định đã chọn: **Q01 C · Q02 C · Q03 C · Q04 C**.

## Q01 — Mức tái cấu trúc tổng thể

**A — Minimal:** sửa contract/guard/credential, nối worker production, thay route UI theo từng bước; ít migration, ít capability.

**B — Balanced (khuyến nghị):** giữ FastAPI + SQLite + domain revisions/artifacts; tách execution plan/provider registry/context/prompt/job stages; rewrite presentation UI thành workspace chương + inspector; triển khai Gemini + OpenRouter + một runtime local sau P0; Qwen chỉ sau contract test.

**C — Scalable:** provider catalog rộng, routing/circuit breaker đa tầng, event projections, knowledge graph/vector memory và UI nhiều tab/dock.

| Tiêu chí | A | B | C |
|---|---:|---:|---:|
| Complexity | Thấp | Vừa | Cao |
| Migration cost | Thấp | Vừa | Cao |
| Maintainability | Vừa | Cao | Khó kiểm soát |
| UX | Vừa | Cao | Cao cho power user |
| Scalability | Thấp-vừa | Vừa-cao | Cao |
| Time to value | Nhanh | Cân bằng | Chậm |
| Risk | Giữ coupling | Migration có kiểm soát | Scope creep |

**Quyết định:** C — scalable. Phạm vi cho phép provider catalog rộng, routing/circuit breaker đa tầng, event projections, knowledge graph/vector memory và UI nhiều tài liệu; các phần này vẫn phải được triển khai theo milestone có kiểm soát, không bật đồng loạt trong một release.

## Q02 — Workspace UI — Đã chọn C

A màn theo bước; B workspace chương + inspector; **C multi-document tabs/dock — đã chọn**. Chi tiết wireframe, accessibility và trade-off ở [ui-ux-research.md](../research/ui-ux-research.md). UI phải có giới hạn tab, eviction và lưu layout để không biến power-user workspace thành unbounded memory.

## Q03 — Local model runtime — Đã chọn C

A Ollama; B LM Studio; **C chưa bật local trong Phase 1 — đã chọn**. Provider cloud/hosted vẫn phải qua guard; local runtime chỉ mở sau một ADR và benchmark RAM/quality riêng.

## Q04 — Quality profile — Đã chọn C

A Economy; B Balanced; **C Quality/Maximum opt-in — đã chọn**. Quality/Maximum cần quote theo stage, hard budget, human review và dừng khi không cải thiện; Economy/Balanced vẫn có thể dùng làm fallback có chủ đích.

## Quy tắc triển khai sau khi khóa

Provider shortlist, structured/vector memory, tab eviction và cost ceiling đã được ghi trong [ADR-0001](adr/0001-locked-subdecisions.md). [Contract bổ sung](../specs/implementation-contracts.md) và [plan v2](../plans/implementation-plan.md) ngày 08/09/2026 khóa chi tiết bàn giao. Không hỏi lại Q01–Q04; thay đổi quyết định đã chốt cần ADR mới. Mô tả Option B phía trên là phương án lịch sử không được chọn, không phải rollout Phase 1 hiện hành.
