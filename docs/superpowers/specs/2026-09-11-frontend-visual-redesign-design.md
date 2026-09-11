# Thiết kế — Thay toàn bộ tầng trình bày frontend

**Ngày:** 2026-09-11 · **Trạng thái:** chờ duyệt · **Nhánh nền:** `main` @ `9ef4fee`

**Phạm vi:** rewrite presentation, giữ API/domain invariants — đúng dòng phạm vi của
[ui-ux-spec.md](../../specs/ui-ux-spec.md). Không đổi API call, state, routing behavior, business guard,
copy người dùng, hay bất kỳ thứ gì thuộc backend.

## 1. Bối cảnh

`implementation-plan.md` §M5 định nghĩa U01–U10. Các task đó **đã làm xong phần chức năng** và đã được
merge vào `main` (153 commit, `4a9598d..9ef4fee`, fast-forward). Nhưng **tầng trình bày mà plan đặt tên thì
chưa hề được giao.** Đây là bằng chứng đo trên cây tại `9ef4fee`, không phải suy đoán:

| # | Bằng chứng | Lệnh / kết quả |
|---|---|---|
| 1 | Không có file CSS nào trong frontend | `frontend/src/**/*.css` → `No files found` |
| 2 | `frontend/src/styles/` — thư mục U01 **tự khai là output** — không tồn tại | `ls frontend/src/styles` → `No such file or directory` |
| 3 | 12 primitive U01 **không màn hình nào import** | grep `from '.*shared/ui'` trên `frontend/src` → `No matches found` |
| 4 | `UiTheme`/`colors[` chỉ sống trong `shared/ui/` | 46 lần, 15 file, **tất cả** trong `shared/ui/` + test của nó |
| 5 | Theme preference lưu nhưng không áp vào DOM | grep `documentElement\|matchMedia\|data-theme` → `No matches found`; `AppearanceSettings.tsx:51` chỉ ghi `localStorage` |
| 6 | `router.tsx` vẫn nguyên khối, giữ palette riêng hardcode | 1374 dòng; bundle build ra **0 asset CSS** |

Hệ quả: có **ba** hệ màu song song — `shared/ui/tokens.ts`, hex hardcode trong `router.tsx` (còn cả gradient
indigo `#4f46e5 → #7c3aed`), và bảng màu C07. Không hệ nào là nguồn sự thật. `AppearanceSettings` là
control chết: chọn Dark, DOM không đổi.

Vì vậy "U01 DONE (code) · browser audit PASS" trong `progress-upgrade-plan.md` đúng theo nghĩa *primitive
đứng một mình có test xanh*, **không** phải *đã áp vào app*. Việc này không làm lại việc plan đã làm — nó
giao phần plan đã đặt tên nhưng chưa giao.

## 2. Quyết định đã chốt

| Hạng mục | Chốt | Ghi chú |
|---|---|---|
| Phạm vi | Giữ logic, thay hết visual | Chủ sở hữu chọn |
| Hướng | Brutalist kỹ thuật (B) + Studio tối (C) | Lưới cứng, kẻ 1px, chữ hoa cho nhãn, mono cho mã/chi phí/diagnostics |
| Theme | **Tối là mặc định, vẫn giữ bản sáng** | Cùng hệ, đảo palette — xem §2.4 |
| Font | **Font hệ thống — không download** | Khớp C07 "Không tự download font"; không cần ADR cho phần này |
| Palette | **ADR-0002 ghi đè cột dark của C07** | Cột sáng giữ nguyên văn C07 |
| CSS | CSS Modules + custom properties | Vite có sẵn — **0 dependency mới** |

### 2.1 Vì sao cần ADR-0002, và phạm vi của nó hẹp

C07 khóa 9 giá trị màu cho dark. Đọc kỹ thì C07 **không áp thẳng được**: C07 ghi `accent #93C5FD` cho dark
— đó là màu **chữ/link/focus** (xanh nhạt trên nền tối), không phải màu **nền nút**. Đem `#93C5FD` làm nền
nút thì nhãn trắng trên nó trượt contrast. `tokens.ts` hiện tại đã lách đúng chỗ đó bằng `#2563eb`, tức
chính nó cũng đã không tuân C07.

Phạm vi ghi đè **chỉ cột dark**. Toàn bộ cột sáng của C07 giữ nguyên từng giá trị, và phần font/type/spacing
của C07 giữ nguyên hoàn toàn. ADR-0001 dòng cuối cho phép mở ADR mới khi evidence mâu thuẫn, chỉ cấm sửa
quyết định âm thầm — spec này là bản ghi công khai của việc mâu thuẫn đó.

### 2.2 Hai token border, không một

Đo mới phát hiện: hairline `#262A31` trên dark `surface #101216` chỉ đạt **1.30:1**.

WCAG 1.4.11 đòi 3:1 cho "visual information required to identify user interface components and states".
Đường kẻ phân cách hàng/bảng **không phải** thành phần giao diện; viền ô nhập **là**. Nên tách đôi:

- `borderSubtle` — kẻ phân cách, minh hoạ. **Miễn 1.4.11.** Dark `#262A31`, light `#D1D5DB`.
- `borderControl` — viền ô nhập/nút, nơi đường viền là thứ duy nhất chỉ ra control tồn tại. **Phải ≥3:1.**
  Dark `#616A78` (3.43:1 trên surface, 3.22:1 trên surfaceRaised), light `#6B7280` (4.83:1).

Gate hiện tại `tokens-contrast.test.ts` kiểm 5 cặp và **bỏ sót chính `border`**. Gate mới kiểm 17 cặp.

### 2.4 "Tối là mặc định" đòi đổi một dòng trong `uiPreferences.ts`

`defaultPreferences()` hiện trả `theme: 'system'`. Nghĩa là người dùng mới trên máy đang bật light mode sẽ
nhận **giao diện sáng** — trái với "tối là mặc định". Không có cách nào giữ nguyên `'system'` mà vẫn tối
mặc định.

Nên: `defaultPreferences()` đổi `theme: 'system'` → `theme: 'dark'`. `'system'` **vẫn là lựa chọn** trong
`THEMES`, chỉ không còn là mặc định.

Hệ quả: **không có test nào đỏ.** `uiPreferences.test.ts` không chứa chuỗi `'system'` nào — nó so với
chính `defaultPreferences()` (dòng 56, 61, 62 dùng `toEqual(defaultPreferences())`), nên đổi giá trị bên
trong hàm không làm lệch phép so. Đây là chỗ **duy nhất** trong đợt này `uiPreferences.ts` bị sửa, và nó
là một dòng.

Nếu giữ `'system'` làm mặc định thì "tối mặc định" chỉ đúng với người dùng đang bật dark mode trên hệ
điều hành — không phải điều đã chốt.

### 2.3 Bảng token

**Dark — mặc định.** Số đo trên `surface #101216`:

| Token | Giá trị | Đo |
|---|---|---|
| `background` / `surface` / `surfaceRaised` | `#0B0C0E` / `#101216` / `#16191E` | — |
| `borderSubtle` / `borderControl` | `#262A31` / `#616A78` | 1.30:1 · **3.43:1** |
| `text` / `textMuted` / `textSubtle` | `#E8EAED` / `#9AA1AB` / `#7A828D` | 15.55 · 7.20 · 4.83 |
| `primary` / `textOnPrimary` / `primaryHover` | `#FF6B2C` / `#0B0C0E` / `#FF8551` | nhãn trên nền nút **6.89:1** (hover 8.13) |
| `danger` / `dangerHover` | `#FF5A3C` / `#FF7A61` | 6.05 |
| `success` / `warning` | `#4ADE80` / `#FBBF24` | 10.76 · 11.23 |
| `focusRing` | `#7DD3FC` | 11.24 |

**Light — C07 nguyên văn.** `background #F7F8FA`, `surface #FFFFFF`, `borderControl #6B7280` (4.83),
`text #172033` (16.27), `textMuted #4B5563` (7.56), `primary #1D4ED8` (6.70), `danger #B91C1C` (6.47),
`success #166534` (7.13), `warning #92400E` (7.09), `focusRing #1D4ED8` (6.70).
C07 chỉ định nghĩa `background`, `surface`, `border`, `text`, `textMuted`, `accent`, `success`, `warning`,
`danger`, `focus`. Các token còn lại spec bổ sung, giữ đúng sắc độ C07: `surfaceRaised #F1F3F7`,
`borderSubtle #D1D5DB`, `textSubtle #5C6675` (5.81:1), `borderControl #6B7280` (lấy từ `border` của C07),
`primaryHover #1E40AF`, `dangerHover #991B1B`, `textOnPrimary #FFFFFF`.

**Tổng: 34 cặp đo (17 cặp × 2 theme), tất cả PASS.** Đo bằng công thức WCAG 2.x relative luminance, so
đúng cặp `foreground`/`background` của từng mục đích dùng.

## 3. Kiến trúc

### 3.1 Tầng token — `frontend/src/styles/`

Thư mục U01 tự khai là output mà chưa tồn tại.

- `tokens.css` — custom properties. `:root` giữ **bộ tối** (đúng mặc định, và đúng cả khi JS chưa chạy);
  `[data-theme="light"]` giữ bộ sáng. **Không dùng `@media (prefers-color-scheme)` cho theme.**

  Lý do bỏ media query: nếu CSS tự đổi theo hệ điều hành thì theme đã ghim và theme hệ thống đánh nhau, và
  xử lý đúng đòi `:root:not([data-theme='light'])` cùng loại logic loại trừ dễ sai. Thay vào đó
  `ThemeProvider` phân giải `system` bằng `matchMedia` và **luôn ghi** `data-theme` là `'dark'` hoặc
  `'light'` — một nguồn sự thật duy nhất, CSS chỉ đọc thuộc tính. `ThemeProvider` nghe
  `matchMedia` change khi (và chỉ khi) preference là `system`.
- `reset.css`, `base.css` — reset tối thiểu; `base.css` đặt `rem` làm đơn vị duy nhất cho type và spacing.
- Biến phụ thuộc preference: `--density-row`, `--font-scale`, và `@media (prefers-reduced-motion: reduce)`
  là **mặc định**, không phải lựa chọn duy nhất — cờ `reduceMotion` của người dùng chỉ có thể tắt thêm
  animation, không thể bật lại khi hệ điều hành đã yêu cầu giảm.

### 3.2 `ThemeProvider`

Đọc `uiPreferences`, ghi `documentElement.dataset.theme`, nghe `matchMedia('(prefers-color-scheme: dark)')`
khi theme = `system`. **Không sửa `uiPreferences.ts`** — chỉ đọc. Đây là mảnh nối khiến `AppearanceSettings`
hết chết.

`index.html` nhận một **boot script inline** đọc `localStorage` và ghim `data-theme` **trước first paint**.
Không có nó, app nháy trắng rồi mới sang tối. Script này chỉ đọc một khoá, không ghi gì.

### 3.3 Primitive

12 primitive trong `shared/ui/` (Button, Input, Select, Combobox, Modal, Drawer, Tooltip, Toast, Tabs,
Table, Tree, Progress) chuyển sang `X.module.css` đọc `var(--…)`.

- Bỏ prop `theme` (mọi primitive đang mặc định `theme='light'` và không caller nào truyền).
- Giữ nguyên phần còn lại của API công khai để 24 test hiện có tiếp tục là hợp đồng:
  `aria-busy` khi loading, `type="button"`, `aria-invalid` + `role="alert"`, `role="option"`, wire
  label/error/description id.
- `radius` 6/8 → **0**. `tokens.ts` thành nguồn duy nhất, export đúng bảng ở §2.3.

### 3.4 Màn hình

`router.tsx` 1374 dòng tách thành `routes/screens/*.tsx` + `.module.css` cạnh nó. Object `styles` inline
hiện tại xoá hẳn.

**Guard đi nguyên, không đổi một dòng logic:**

- `STALE_MASTER_CODES` (`MASTER_HASH_MISMATCH`, `MASTER_ARTIFACT_NOT_READY`, `MASTER_TRANSLATION_STALE`,
  `MASTER_VOICE_PLAN_STALE`, `MASTER_PROBE_HASH_MISMATCH`) — chặn duyệt master cũ (A05).
- `TRANSLATION_QA_BLOCKERS_OPEN` — chặn duyệt khi QA còn blocker.
- Hai `useMatch` gọi **vô điều kiện** trong `Shell` — bản cũ gọi `useMatch(a) ?? useMatch(b)` làm số lượng
  hook đổi khi chuyển route project → chapter, React throw giữa render và sập app (lỗi thật do U03 E2E
  tìm ra, sửa tại `171d2d6`).
- Consent + budget authorization gate trước mọi lời gọi cloud.
- `data-testid="master-audio"`, `data-testid="export-workflow"`, `data-testid="manifest-export-private-1"`
  — test đọc chúng.

## 4. Chia giai đoạn

Sáu giai đoạn, không giai đoạn nào phụ thuộc giai đoạn sau. Mỗi giai đoạn tự nghiệm thu được.

**G1 · Token + theme.** `styles/*`, `ThemeProvider`, boot script, `tokens.ts` viết lại,
`tokens-contrast.test.ts` 5 → 17 cặp, và dòng `theme` trong `defaultPreferences()` (§2.4).
ADR đã viết kèm spec này: [ADR-0002](../../architecture/adr/0002-dark-palette-and-border-tokens.md).
*Cổng:* contrast 17 cặp × 2 theme xanh; test mới khẳng định đổi theme thì `dataset.theme` đổi thật;
272 test cũ vẫn xanh.

**G2 · 12 primitive sang CSS Modules.** Bỏ prop `theme`, giữ API + ARIA.
*Cổng:* 24 test `shared/ui` xanh.
*Rủi ro thấp nhất trong cả sáu giai đoạn* — grep đã chứng minh không màn hình nào import `shared/ui`, nên
đổi chúng **không thể** làm vỡ screen.

**G3 · density + fontScale + reduceMotion áp thật.** Hiện `uiPreferences` lưu cả ba, không cái nào chạm DOM.
*Cổng:* đổi preference thì CSS var trên root đổi; `prefers-reduced-motion: reduce` được tôn trọng.

**G4 · Tách `router.tsx`.** Thuần cơ học. **Không đổi hành vi, không đổi visual.**
Làm trước G5 vì ngược lại là restyle code sắp phải di chuyển.
*Cổng chặn:* 9 spec E2E xanh **y hệt trước và sau** — đây là bằng chứng trung tính hành vi.

**G5 · Visual từng màn.** Thứ tự theo IA đã khóa: Global (Thư viện/Công việc/Cài đặt) → Project
(Chương/Thuật ngữ/Nhân vật/Audio/Xuất) → workspace 3 pane. Xoá `styles` inline cũ.

**G6 · Responsive + a11y.** 320/390/1024/1366, inspector xuống drawer dưới 1024px, text 200%, CJK/IME.

## 5. Nghiệm thu

| Cổng | Lệnh | Ngưỡng |
|---|---|---|
| Unit frontend | `npx vitest run` | 272 hiện có **không giảm**; test mới thêm |
| Build | `npm run build` | PASS, tsc sạch |
| Contrast | `tokens-contrast.test.ts` | 17 cặp × 2 theme, text ≥4.5:1, UI/focus ≥3:1 |
| Browser E2E | `npx playwright test` (Chrome hệ thống) | 9 spec xanh |
| Backend không hồi quy | `pytest backend/tests -q` | 1258 passed — chạy **một lần** ở cuối; giai đoạn này không chạm backend |
| Reflow | `visual-a11y.spec.ts` | 320/390/1024/1366 không cuộn ngang |
| WCAG 1.4.4 | `visual-a11y.spec.ts` | text 200% ở 1280/1024 không overflow, không cắt chữ |
| Việt/CJK | `visual-a11y.spec.ts` | không mojibake, CJK không bị `uppercase` |

## 6. Ràng buộc cứng

- **Copy người dùng đóng băng.** `App.test.tsx` assert trực tiếp vào chữ hiển thị và accessible name.
  Danh sách dưới lấy nguyên văn từ file, không phải từ trí nhớ:
  heading `'Truyện Audio Studio'`; button `'Tạo dự án'`, `'Phê duyệt audio'`, `'Nghe bản mới'`,
  `'Chọn giọng này'`, `'Nghe thử'`, `'Dịch bằng Qwen'`, `'Lưu API key vào profile'`, `'Preview folder'`,
  `'Confirm import mapping'`, `'Lấy báo giá'`, `'Tạo archive riêng tư'`, `'Hủy job'`, `'Thử lại'`;
  label `'Tên truyện'`, `'Loại nguồn'`, `'Trạng thái quyền'`, `'Jobs overlay'`, `'Cloud consent ID'`,
  `'Budget authorization ID'`, `'Qwen profile ID'`, `'Gemini profile ID'`, `'Local folder path'`,
  `'Chế độ'`, `'Nghe thử và chọn giọng'`; region `'Nghe master'`; heading `'Translation quality'`;
  text `'Chưa có job đang chạy'`, `'chapter-1.txt'`, `'Preset voice-1'`, `'Qwen source'`,
  `'Qwen target'`, `'Sẵn sàng xuất bản'`, `'Checksum khớp'`, `'Đã tạo archive riêng tư.'`,
  `'Cần cài model và license.'`, `'Đã tải bản dịch hiện tại'`,
  `'Qwen cần consent cloud và budget authorization đã tạo trước.'`,
  `'Cần bật opt-in trước khi lấy báo giá cho Quality/Maximum.'`;
  regex `/Master abc123abc123/`, `/Bản master trên máy chủ đã thay đổi/`, `/studio không tự upload/`.
  Đổi chữ = đỏ test. Test là hợp đồng, không phải vật cản.
- **Không chạm** API call, state, routing behavior, guard, backend.
- **Không thêm dependency. Không download font.**
- **IA khóa theo `ui-ux-spec.md`:** navigator 240px · inspector 320px · split clamp 25–75% ·
  tối đa **8 tab** tài liệu (ADR-0001 D4) · dưới 1024px inspector thành drawer, source/translation thành tab.
- **Không có màu nào là tín hiệu duy nhất** — trạng thái phải kèm chữ hoặc hình, không chỉ đổi màu.

## 7. Rủi ro

| Rủi ro | Vì sao nguy hiểm | Chặn |
|---|---|---|
| G4 làm mất guard âm thầm | Không test nào bắt được nếu chỉ đổi cấu trúc | Chạy E2E lấy baseline **trước** G4, so lại sau |
| `uppercase` giết chữ CJK | Brutalist dùng chữ hoa nhiều; `text-transform: uppercase` vô nghĩa với Hán tự và hỏng dấu tiếng Việt | Luật cứng: **không bao giờ uppercase nội dung nguồn/đích**, chỉ uppercase nhãn giao diện |
| Nháy theme khi tải | JS chạy sau first paint | Boot script inline ở §3.2 |
| Boot script và `ThemeProvider` lệch nhau | Boot script đọc `localStorage` thô, provider parse qua `uiPreferences`; hai bên phân giải khác nhau thì đổi theme sẽ giật | Boot script chỉ **đọc một khoá** và ghi `data-theme`; provider ghi lại ngay sau mount. Cả hai gọi **cùng** một hàm phân giải, không chép logic |
| Zoom 200% phá lưới cứng | Lưới pixel cố định không giãn | `rem` cho type và spacing; test ở 1280 và 1024 |
| Primitive và screen lệch nhau | Sau G2 primitive đổi nhưng screen chưa dùng | G5 bắt buộc dùng primitive, cấm hex trực tiếp trong `screens/*.module.css` |

## 8. Không nằm trong phạm vi

Việc này **không** đóng các mục còn nợ của plan. Theo `progress-upgrade-plan.md`:

- **PARTIAL:** J01, J04, U06, U07, U08, U09, X07.
- **NOT_STARTED:** R03 (deferred có chủ đích sau phát hành).
- **NOT_RUN/BLOCKED — toàn bộ nhóm cần môi trường live:** cloud trả phí có consent/budget
  (REVIEW/REPAIR/SUMMARIZE handler, live smoke), model VieNeu + license (A01/A02/A03/A04 live,
  nghe kiểm chất lượng), FFmpeg thật (binary không có trên máy), corpus có quyền + reviewer (V03).

Spec này thay **tầng trình bày** của các mục đó. Nó không làm chúng thành DONE, và tài liệu nghiệm thu sẽ
không ghi chúng là DONE.

Cũng không nằm trong phạm vi: xoá `codex/implement-upgrade-plan`, `codex/upgrade-code` và worktree
`.worktrees/upgrade-code`; dọn `.superpowers/brainstorm/` (untracked, cố ý không add); thêm `.omc/` vào
`.gitignore`.
