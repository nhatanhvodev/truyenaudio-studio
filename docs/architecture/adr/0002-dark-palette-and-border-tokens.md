# ADR-0002 — Ghi đè cột dark của C07 và tách token border

**Status:** Accepted · **Date:** 2026-09-11 · **Owner:** Local studio owner

## Context

ADR-0001 kết luận: "Nếu evidence mâu thuẫn, mở ADR mới, không sửa quyết định âm thầm." ADR này là bản ghi
công khai của một mâu thuẫn như vậy.

`implementation-contracts.md` C07 khóa 9 giá trị màu cho theme dark, kèm ghi chú rằng đó là *"token thiết kế
cần kiểm contrast, không phải bằng chứng đã render đạt WCAG"*. Hai vấn đề xuất hiện khi đo:

**Thứ nhất, C07 dark không áp thẳng được.** C07 ghi `accent #93C5FD` cho dark. Đó là màu chữ/link/focus —
xanh nhạt trên nền tối. Khi dùng làm **nền nút** thì nhãn trắng trên nó không đạt 4.5:1. Bằng chứng rằng
điều này đã được nhận ra trước đó mà không được ghi lại: `frontend/src/shared/ui/tokens.ts` — code đang chạy
trên `main` — dùng `primary: '#2563eb'` cho dark, tức **chính nó đã không tuân C07**. Một quyết định đã bị
sửa âm thầm, đúng thứ ADR-0001 cấm.

**Thứ hai, C07 chỉ có một token `border`.** Với theme dark, một token duy nhất không phục vụ được hai mục
đích có yêu cầu khác nhau: đường kẻ phân cách hàng/bảng và đường viền ô nhập.

Đo trên máy tham chiếu (công thức WCAG 2.x relative luminance):

```
dark  border #262A31 / surface #101216  =  1.30:1    (ngưỡng UI: 3:1)  FAIL  — palette đề xuất
dark  border #4b5563 / surface #1f2937  =  1.94:1    FAIL              — tokens.ts đang chạy trên main
dark  border #9CA3AF / surface #1F2937  =  5.78:1    PASS              — C07, nhưng quá nặng cho kẻ 1px
```

Dòng thứ hai đáng chú ý: `tokens.ts` hiện tại **cũng không đạt** 3:1 cho viền ô nhập. Nó chưa bị phát hiện
vì gate cũ không kiểm `border` — xem D5.

Bối cảnh thi công: việc này nằm trong đợt thay toàn bộ tầng trình bày frontend
([spec](../../superpowers/specs/2026-09-11-frontend-visual-redesign-design.md)). Chủ sở hữu chọn hướng
brutalist kỹ thuật + studio tối, tối là mặc định, giữ bản sáng, **font hệ thống không download** (khớp C07).
Khi đó `frontend/src/**/*.css` không có file nào, `frontend/src/styles/` — thư mục U01 tự khai là output —
không tồn tại, và 12 primitive trong `shared/ui/` không được màn hình nào import.

## Decisions

### D1 — Mặc định tối, giữ bản sáng

Theme mặc định là `dark`. Bản sáng vẫn được giữ và giữ **nguyên văn từng giá trị của C07**. Không có
dark-only: bỏ bản sáng sẽ làm mất lựa chọn của người dùng đang dùng nền sáng.

### D2 — Ghi đè cột dark, phạm vi hẹp

Chỉ **cột dark** của C07 bị thay. Cột sáng giữ nguyên toàn bộ. Phần font/type/spacing của C07 giữ nguyên
hoàn toàn — bao gồm luật "Không tự download font", nên không cần ADR riêng cho font.

Bảng dark mới:

| Token | Giá trị | Đo trên `surface #101216` |
|---|---|---|
| `background` / `surface` / `surfaceRaised` | `#0B0C0E` / `#101216` / `#16191E` | — |
| `text` / `textMuted` / `textSubtle` | `#E8EAED` / `#9AA1AB` / `#7A828D` | 15.55 · 7.20 · 4.83 |
| `primary` / `textOnPrimary` / `primaryHover` | `#FF6B2C` / `#0B0C0E` / `#FF8551` | nhãn trên nền nút 6.89 (hover 8.13) |
| `danger` / `dangerHover` | `#FF5A3C` / `#FF7A61` | 6.05 |
| `success` / `warning` | `#4ADE80` / `#FBBF24` | 10.76 · 11.23 |
| `focusRing` | `#7DD3FC` | 11.24 |

`textOnPrimary` là **màu tối** trên nền cam — đảo chiều so với quy ước "chữ trắng trên nút màu". Cam
`#FF6B2C` là màu sáng; chữ trắng trên nó chỉ đạt **2.84:1**, không đủ 4.5:1.

### D3 — Tách `border` thành hai token

C07 có một `border`. ADR này tách đôi theo đúng phạm vi áp dụng của WCAG 1.4.11 —
*"visual information required to identify user interface components and states"*:

- **`borderSubtle`** — đường kẻ phân cách hàng, bảng, panel. **Không phải** thành phần giao diện, nên miễn
  1.4.11. Dark `#262A31` (1.30:1), light `#D1D5DB`.
- **`borderControl`** — viền ô nhập, nút, checkbox, nơi đường viền là thứ duy nhất chỉ ra control tồn tại.
  **Phải ≥3:1.** Dark `#616A78` (**3.43:1** trên surface, 3.22:1 trên surfaceRaised), light `#6B7280`
  (C07 nguyên văn, 4.83:1).

Ghi chú kỹ thuật: `borderControl` dark `#616A78` thấp hơn `#9CA3AF` của C07. C07 đúng về ngưỡng nhưng
`#9CA3AF` cho một đường viền 1px trông như khối đặc, phá vỡ ngôn ngữ thị giác đã chọn. `#616A78` là giá
trị **nhỏ nhất** đạt 3:1 trên **cả hai** surface và surfaceRaised, nên nó là biên dưới chứ không phải lựa
chọn tuỳ ý.

### D4 — CSS custom properties, không thêm dependency

Token sống trong `frontend/src/styles/tokens.css` dưới dạng custom property. Vite hỗ trợ CSS Modules sẵn,
nên đợt thi công này thêm **0 dependency**. Không dùng thư viện CSS-in-JS hay design system ngoài.

### D5 — Gate contrast mở rộng từ 5 lên 17 cặp

`tokens-contrast.test.ts` hiện kiểm 5 cặp và **bỏ sót chính `border`**. Hệ quả đã xảy ra thật: `tokens.ts`
trên `main` đang chạy với `border` chỉ đạt **1.94:1** — dưới cả ngưỡng 3:1 — và gate vẫn xanh, vì nó không
hề kiểm cặp đó. Gate mới kiểm 17 cặp mỗi theme, gồm `borderControl` trên cả surface và surfaceRaised,
`textOnPrimary` trên cả `primary` và `primaryHover`, và `text` trên `surfaceRaised`.

Đo lại toàn bộ: **34 cặp (17 × 2 theme), tất cả PASS.**

## Consequences

Đợt thi công có nguồn màu duy nhất thay cho ba hệ song song hiện tại (`tokens.ts`, hex hardcode trong
`router.tsx`, C07). `AppearanceSettings` từ chỗ là control chết — lưu `localStorage` mà không ai đọc — trở
thành control thật.

Đổi lại: hai token border đòi người viết CSS phải chọn đúng token cho đúng mục đích, và chọn sai thì không
test nào bắt được (gate chỉ kiểm `borderControl`). Đây là rủi ro tri thức, không phải rủi ro kỹ thuật.

Cột dark của C07 từ nay không còn là nguồn sự thật. Ai đọc C07 để tìm mã màu dark phải được dẫn sang ADR
này. `implementation-contracts.md` C07 cần một dòng trỏ tới ADR-0002 ở mục dark.

Palette cam `#FF6B2C` là màu tín hiệu đơn nhất; nó không mã hoá trạng thái. Trạng thái vẫn phải kèm chữ
hoặc hình, không dùng màu làm tín hiệu duy nhất.

## Rejected alternatives

- **Giữ nguyên C07 dark, chỉ tách `accent` thành `accentText`/`accentFill`.** Ít rủi ro governance nhất,
  nhưng giữ `#111827`/`#1F2937` (xám xanh Tailwind) làm nền — trùng với chính palette mặc định mà đợt này
  thay, nên không đạt mục tiêu thị giác.
- **Dark-only, bỏ bản sáng.** Đơn giản nhất, nhưng app hiện mặc định sáng và sẽ mất lựa chọn của người
  dùng đang dùng nền sáng.
- **Dùng `#9CA3AF` của C07 cho border.** Đạt 7.02:1, vượt xa ngưỡng, nhưng biến mọi đường kẻ 1px thành
  khối xám đặc — phá ngôn ngữ thị giác đã chọn.
- **Một token `border` duy nhất, hạ ngưỡng gate xuống 1.3:1.** Hợp thức hoá một giá trị không đạt cho ô
  nhập, chỉ để tránh phải tách token.
- **Thêm thư viện design system ngoài.** Trái ràng buộc "chưa thêm thư viện nếu primitive hiện có đáp ứng"
  của plan §U01.
- **Download font (Archivo/JetBrains Mono).** Trái C07 "Không tự download font". Font hệ thống đã phủ đủ
  dấu tiếng Việt.

## Verification

- Gate tự động: `tokens-contrast.test.ts` — 17 cặp × 2 theme, text ≥4.5:1, UI/focus ≥3:1.
- Gate hồi quy: `npx vitest run` (272 test hiện có không được giảm), `npm run build`, `npx playwright test`
  (9 spec trên Chrome hệ thống).
- Gate không hồi quy backend: `pytest backend/tests -q` → 1258 passed. Đợt thi công này không chạm backend.
- Kiểm thủ công: `AppearanceSettings` đổi theme thì `documentElement.dataset.theme` đổi thật — đây là hành
  vi mà ADR-0001 D4 giả định đã có nhưng trên `main` @ `9ef4fee` chưa hề tồn tại.
- Thiết kế đầy đủ: [spec 2026-09-11](../../superpowers/specs/2026-09-11-frontend-visual-redesign-design.md).
