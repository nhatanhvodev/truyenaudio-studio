# UI/UX Spec (LOCKED BASELINE; Q02 C)

Phạm vi: rewrite presentation, giữ API/domain invariants. Đã chọn multi-document tabs/dock; xem [ui-ux-research](../research/ui-ux-research.md) và Q02. [Contract bổ sung C07](implementation-contracts.md) chốt token màu/font/component states, dock lifecycle/8-tab/eviction và đủ bảy nhóm Settings; C03–C05 chốt draft/layout API và streaming. Task U01–U10 của plan là phạm vi bàn giao bắt buộc.

## IA và layout

Global: Thư viện, Công việc, Cài đặt. Project: Chương, Thuật ngữ, Nhân vật, Audio, Xuất. Workspace desktop gồm chapter navigator trái, source/translation giữa, inspector phải; dưới 1024px inspector thành drawer và source/translation thành tabs.

## States

Mọi resource có loading/empty/error/stale/success. Job công khai dùng queued/running/cancelRequested/cancelled/succeeded/failed theo C05; mapping trạng thái nội bộ hiện hữu nằm compatibility layer. Audio playing và selected độc lập. Hash/ID nằm trong chi tiết diagnostics, không thay copy người dùng.

## Accessibility

WCAG 2.2 AA cho flow chính: focus visible/not obscured, keyboard, screen reader status, 320px reflow, CJK/IME, reduced motion, contrast đo được. Tree chỉ dùng khi hierarchy thật; combobox theo WAI-ARIA APG.

## Acceptance

Người dùng hoàn tất import → dịch → sửa → duyệt → nghe sample → chọn voice → nghe master → export bằng keyboard; đổi chapter không mất draft; conflict không ghi đè; list 10.000 chapter không nạp toàn văn; player seek qua HTTP range.
