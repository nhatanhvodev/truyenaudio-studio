# Nghiên cứu UI/UX cho Truyện Audio Studio

Ngày đối chiếu: 07/09/2026. Phạm vi: đọc source, test và tài liệu gốc; không triển khai, không cài dependency, không khởi chạy API/worker, không gọi LLM/TTS. Các phương án dưới đây là **đề xuất để duyệt**, chưa khóa thiết kế.

## 1. Kết luận từ discovery

Sản phẩm hiện tại là workspace cá nhân để nhập truyện, dịch, duyệt, tạo audio và xuất gói thủ công. Đây là ứng dụng làm việc nhiều bước với văn bản dài. Màn hình trọng tâm nên là chương đang hiệu đính, có ngữ cảnh dự án và khả năng quay lại công việc dang dở. Mô hình landing page, dashboard chỉ số hoặc trang quảng bá provider không phù hợp.

Đã đọc `TasteSkill` tại `C:/Users/nhata/.codex/skills/taste-skill/SKILL.md`. Chính skill xác định dashboard, dense product UI và multi-step product UI nằm ngoài phạm vi chính (phần mở đầu và §13). Vì vậy chỉ áp dụng nguyên tắc đọc brief, audit trước redesign, typography dễ đọc, token nhất quán, motion có mục đích, trạng thái và accessibility. Không áp dụng hero, ảnh trang trí, cinematic motion, luật giới hạn bảng dữ liệu hay cài thư viện của marketing. Hướng tham khảo: workspace hiệu đính yên tĩnh; độ biến thiên thấp, motion thấp, mật độ vừa. Đây là định hướng, không phải bản UI đã render.

### Nguồn code và các màn hình thực sự được nối

Các đường dẫn trong bảng tính từ repository root. Line là vị trí source đọc trực tiếp ngày đối chiếu, không phải ảnh chụp runtime.

| Bằng chứng | Hành vi hiện tại | Hệ quả thiết kế |
|---|---|---|
| `frontend/src/App.tsx:1-5`; `frontend/src/routes/router.tsx:85-105` | App dùng RouterProvider; route chương chứa inline `TranslationScreen`, `VoiceScreen`, `AudioScreen`, `ExportScreen` | Không đánh đồng component trong thư mục features với tính năng người dùng đã truy cập được |
| `router.tsx:109-119` | Nav Dự án / Jobs / Diagnostics, JobProgress nằm trong shell | Chưa có cấu trúc dự án xuyên suốt và nav riêng cho thuật ngữ, nhân vật, models |
| `features/projects/ProjectWizard.tsx:61-73,198,313` | Tải `/api/projects`, render projects và chapters bằng map; lỗi load bị bỏ qua | Library cần lỗi tải khác trạng thái rỗng; danh sách chương lớn cần API summary/paging |
| `router.tsx:138-205` | Preview import rồi xác nhận mapping, điều hướng chương đầu | Giữ review import; bổ sung quay lại danh sách chương và kết quả từng nguồn |
| `router.tsx:278-301,325-359,538-548` | Key Gemini được giữ trong localStorage; model list và lời mô tả chất lượng hard-code | Cần Settings credentials/backend; picker có nguồn metadata và ngày kiểm tra; không dùng badge chất lượng chưa benchmark |
| `router.tsx:383-408,662-681` | Qwen yêu cầu người dùng nhập consent ID và budget ID | Đưa thao tác cấp quyền/ước tính vào flow có tên dễ hiểu; ID chỉ ở chi tiết chẩn đoán |
| `router.tsx:412-451` | Sửa đoạn gửi run ID/hash; duyệt cũng kiểm tra hash | Giữ optimistic concurrency, trình bày xung đột dễ hiểu và không làm mất bản nháp |
| `router.tsx:472-478,748-846` | Filter duyệt quét issues theo segment; render mọi đoạn; đánh số index sau filter | Nguy cơ chậm với chương dài; số đoạn sau lọc không còn định danh gốc. Cần ordinal ổn định và index issues |
| `router.tsx:718-745,849-877` | Có hai vị trí bỏ qua blocker; approve thường bị khóa khi blocker còn mở | Hợp nhất review gate, nêu issue cụ thể, lý do override và ảnh hưởng vào revision; không tự bỏ blocker |
| `router.tsx:885-950` | Voice route lấy first active/available, hiện preset ID, bấm render một giọng | Chưa có browse → nghe → so sánh → chọn trong route thật |
| `router.tsx:953-1022` | Audio route load master hash, có approve; không render audio player | Duyệt audio hiện chưa tương đương đã nghe audio; phải nối playback thật |
| `router.tsx:1040-1054,1088-1093` | Export publication hard-code Tập 1, số tập 1; UI hiện tên file/hash, chưa hiện `directoryPath` | Form metadata phải có giá trị dự án/chương; kết quả cần vị trí output và thao tác thủ công rõ |
| `features/batch/BatchQueue.tsx:47-48,72-76,96-106,129-159` | Có cursor paging 25, giới hạn chọn 50; load more cộng dồn; yêu cầu nhập IDs và token estimate | Giữ bounded batch nhưng cần quote/config UX; paging chưa đồng nghĩa bounded DOM |
| `features/jobs/JobProgress.tsx:38-80,83,159-161`; `router.tsx:119,1100-1105` | Snapshot + EventSource, giữ 6 events, overlay fixed; JobsScreen lại mount JobProgress | Source cho thấy hai instance trên `/jobs`, có thể tạo hai subscriptions/overlay; cần một state owner và trang job có lịch sử |
| `frontend/src/shared/api.ts:45-76` | Fetch chung có CSRF và retry một lần cho mọi 403 mutating request; lỗi chuyển thành Error(detail) | Cần typed error và retry theo mã cụ thể. Không suy rằng mọi 403 đều do token |

`features/translation/TranslationEditor.tsx`, `features/voices/VoiceBrowser.tsx`, `features/audio/AudioReview.tsx` chỉ được import bởi test tương ứng trong frontend đã rà; router không import chúng. `ProviderSettings` và `VoiceComparison` được `MultiVoiceCloudDemo` import, không phải màn settings/voice picker production. `VoiceBrowser.tsx:60-78` chỉ enqueue preview và hiện job ID; `VoiceComparison.tsx:19-47` chỉ chọn candidate, không phát sample. Không gọi những component này là một flow preview hoàn chỉnh.

### Mức xác minh

- **Đã xác minh source:** đường route, request, local state, hard-code và sự khác nhau giữa demo với route.
- **Đã đọc test, chưa chạy test:** `App.test.tsx:12-188` có test navigation, Qwen guards, import preview và private export bằng fetch stub. `VoiceBrowser.test.tsx:11-109` mock catalogue/preview; `TranslationEditor.test.tsx:11-146` mock edit/hash conflict/filter. Chúng không chứng minh browser route có các component này.
- `frontend/e2e/single-voice.spec.ts:3-23` gọi flow fake và dùng nhãn `Dịch bằng fake` / `Phê duyệt bản dịch`, khác source route hiện tại. Đây là dấu hiệu E2E cần cập nhật, chưa kết luận chạy fail vì chưa thực thi.
- `frontend/e2e/multivoice-cloud.spec.ts:3-17` thao tác route demo, fake consent và text summary; không phải bằng chứng cloud/TTS thực đã thành công.
- **Chưa visual QA / chưa benchmark:** port 8765 theo `run-studio.bat:11` và port frontend thông dụng không có listener khi kiểm tra. Không start app vì startup/test server có thể ghi dữ liệu. `playwright.config.ts:15-23` tự chạy `scripts/e2e-server.ps1`; không chạy E2E trong phiên nghiên cứu này. Contrast, responsive, playback, FPS/heap và screen reader chưa được kiểm tra runtime.

## 2. Nghiên cứu sản phẩm từ nguồn gốc

| Sản phẩm/chuẩn | Điều đã đối chiếu | Điều học được cho truyện dài |
|---|---|---|
| [OmegaT 6.1, Panes](https://omegat.sourceforge.io/manual-standard/en/chapter.panes.html) | Có editor, fuzzy matches, glossary theo đoạn, tên engine và origin, notes, status/progress | Đặt glossary/nhân vật liên quan cạnh đoạn đang sửa; giữ nguồn gợi ý; cho phép ngữ cảnh khác tạo cách dịch khác. Không tự áp dụng translation memory chỉ vì câu giống nhau |
| [Weblate, Translating](https://docs.weblate.org/en/latest/user/translating.html) | Editor kèm context/history/glossary; save-and-continue qua bàn phím; Zen có top-bottom hoặc side-by-side; search/replace có preview | Cung cấp đọc tập trung và đối chiếu; sửa hàng loạt phải xem diff trước. Không sao chép toàn bộ UX dịch chuỗi phần mềm cho tiểu thuyết |
| [W3C APG Combobox](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/) | Mô tả semantic, popup và keyboard interaction của combobox | Model picker có tìm kiếm phải quản lý focus/active option đúng; chọn option không tự chạy model |
| [W3C APG Tree View](https://www.w3.org/WAI/ARIA/apg/patterns/treeview/) | Phân biệt focus và selection; keyboard navigation theo hierarchy | Chỉ dùng tree khi truyện có tập/chương lồng nhau; danh sách chương phẳng dùng list đơn giản |

Đây là nguồn cho pattern thao tác, không chứng minh phương án đề xuất nhanh hơn UI cũ hoặc giúp bản dịch hay hơn. Những kết quả đó cần benchmark và usability test sau khi có prototype được duyệt.

## 3. Vấn đề và mức ưu tiên

| ID | Mức | Vấn đề/nguyên nhân | Tác động | Khuyến nghị nghiên cứu |
|---|---|---|---|---|
| UX01 | High | Credentials lẫn vào editor và lưu browser | Key tồn tại ngoài vault, lặp cấu hình, khó thấy kết nối thật | Backend credential reference; editor chỉ chọn profile |
| UX02 | High | Flow giọng/audio thiếu playback trên route | Người dùng không nghe trước khi chọn hoặc duyệt | Một flow audio có artifact player, trạng thái sample và select riêng |
| UX03 | High | Discovery component/demo dễ bị xem là hoàn tất | Redesign có thể nối sai API và kế thừa kiểm thử không phản ánh route | Coverage matrix route → backend → artifact → E2E |
| UX04 | High | Busy một request dài, lỗi tải bị nuốt ở một số route | Không phân biệt chưa dịch, backend lỗi, hay kết quả chưa tải | State machine explicit + jobs có thể khôi phục |
| UX05 | High | Manual guard IDs và estimate | Khó bắt đầu cloud/batch đúng, dễ nhập sai | Quote và consent theo provider/scope; mở chi tiết mới thấy IDs |
| UX06 | Medium | Cả chương/all chapters render trong collection | RAM/DOM tăng khi truyện lớn; chưa có số đo | Paged summary + bounded editor; benchmark 1.000/10.000 chương |
| UX07 | Medium | Provider marketing chiếm editor; model quality claims chưa đo | Gián đoạn hiệu đính, nhầm đề xuất với bằng chứng | Toolbar config ngắn; inspector chứa metadata có nguồn |
| UX08 | Medium | Jobs overlay và trang mount trùng component | Có thể trùng stream, che focus/nội dung | Một job store, footer gọn và trang có filter/history |
| UX09 | Medium | Export metadata hard-code | Sai tên/số tập với truyện nhiều chương | Metadata review và defaults từ domain |
| UX10 | Medium | Nhãn Việt/Anh, ID/hash/code lỗi xuất hiện như thao tác chính | Cognitive load và khó học | Copy tiếng Việt; diagnostics giữ mã kỹ thuật có copy |

Không gán Critical chỉ từ quan sát styling. Mức độ an ninh cuối cùng của UX01 cần kết hợp phân tích backend/threat model.

## 4. Ba hướng thiết kế và trade-off

Cả ba hướng đều thiết kế lại hình thức, không giữ design hiện tại chỉ vì đã có code. Minimal ở đây chỉ nói quy mô orchestration và migration.

| Tiêu chí | A: Theo bước, màn đơn | B: Workspace chương + inspector | C: Studio nhiều tài liệu/tab |
|---|---|---|---|
| Mô hình | Import → dịch → duyệt → audio → xuất, mỗi bước riêng | Một project shell, chapter navigator, editor, panel ngữ cảnh | Nhiều tab chương/so sánh, dockable panels, workspace layouts |
| Complexity | Thấp | Vừa | Cao |
| Migration cost | Thấp-vừa | Vừa | Cao |
| Maintainability | Dễ lúc nhỏ; lặp state giữa bước | Tốt nếu tách editor/domain/job state | Khó hơn do layout/session/tab state |
| UX | Dễ học, chuyển bước nhiều | Cân bằng học và hiệu đính thường xuyên | Tốt cho power user; quá tải người mới |
| Scalability | Phụ thuộc paging mỗi màn | Bounded data theo chapter/project | Nhiều tab tăng bộ nhớ, phải eviction |
| Thời gian tương đối | Ngắn nhất | Trung bình | Dài nhất |
| Rủi ro | Fragmented context, nhiều navigation | Cần mobile collapse và focus rõ | Phạm vi rộng, RAM/lỗi đồng bộ tab |

**Khuyến nghị để người dùng duyệt: B.** Dịch truyện dài cần ngữ cảnh liên tục nhưng máy local không nên mang chi phí IDE nhiều tab. A phù hợp nếu ưu tiên hoàn thành bản dùng đơn giản sớm; C chỉ nên mở lại khi B được sử dụng và có nhu cầu đối chiếu nhiều chương thực tế. Không chốt framework UI/editor package từ nghiên cứu này.

### Wireframe chữ A

```text
Dự án / Tên truyện / Chương 12        Công việc | Cài đặt
Nhập > Cấu hình > Dịch & duyệt > Audio > Xuất
[Provider] [Model] [Phong cách]      [Dịch chương]
Gốc / Bản dịch (xếp dọc hoặc hai cột)
[Lưu] [Duyệt]                       [Bước tiếp]
```

### Wireframe chữ B

```text
Thư viện / Tên truyện / Chương 12      Công việc 2 | Cài đặt
Chương + tìm     Bản gốc         Bản dịch            Inspector [đóng]
01 Đã duyệt     Đoạn 18         Đoạn 18             QA | Ngữ cảnh
02 Cần sửa      ...             [bản nháp]           Thuật ngữ liên quan
03 Đang dịch    ...             ...                 Nhân vật / xưng hô
                [Đối chiếu | Đọc] [Model / Profile] [Chi tiết cấu hình]
                Đã lưu 10:42      [Duyệt chương]     Audio | Xuất
```

### Wireframe chữ C

```text
Project navigator | Ch12 [x] | Ch13 [x] | So sánh [x]
Panels tùy chỉnh: source / target / memory / QA / audio timeline
Job monitor và cache trạng thái theo tab, có giới hạn tab hoạt động
```

## 5. IA và flow đề xuất, chưa khóa

Global navigation ngắn: **Thư viện / Công việc / Cài đặt**. Trong dự án: **Chương / Thuật ngữ / Nhân vật / Audio / Xuất**. Models nằm trong Cài đặt AI và mở nhanh từ toolbar chương; tránh Library và Projects cùng là hai điểm đến khó phân biệt. Breadcrumb chứa tên truyện + chương; quay lại phải giữ filter và vị trí.

Core flow đề xuất:

1. Thư viện → tạo/mở dự án → nhập → preview phân chương và báo duplicate/encoding → xác nhận.
2. Cấu hình dịch theo dự án: ngôn ngữ nguồn, profile, provider/model, glossary/context; chương có override rõ và nút về mặc định.
3. Preflight chỉ yêu cầu nội dung thực sự cần: credential status, context/capability, phạm vi dữ liệu cloud, cost estimate/ceiling. Backend trả IDs; người dùng không chép IDs.
4. Gửi job → tiếp tục hiệu đính chương khác → tiến độ theo stage/chunk, hủy/yêu cầu dừng → mở kết quả đúng revision.
5. Review source-target, issues, entity/glossary; save draft → xem xung đột nếu hash đổi → duyệt revision.
6. Chọn một giọng mặc định → nghe sample → preview văn bản của mình → nghe → chọn → enqueue chapter audio.
7. Nghe audio đã ghép, mở đoạn lỗi, render lại phần cần → duyệt artifact tương ứng revision → xem metadata/output destination → xuất gói thủ công.

Điều hướng tới Audio không có nghĩa bản dịch đã được duyệt. Gate backend vẫn là nguồn quyết định; UI dẫn tới đúng bước còn thiếu. Bản dịch thay đổi sau render phải làm rõ audio cũ và yêu cầu xác nhận artifact mới, không hiển thị success cũ cho nội dung mới.

## 6. Workspace và settings

- Hai cột source/target căn theo segment ID, có mode đọc liên tục để đánh giá văn phong. Đoạn có ordinal gốc, số thứ tự không đổi khi lọc QA. Đồng bộ cuộn theo anchor đoạn, không ép cùng pixel khi bản dịch dài hơn nguồn.
- Inspector chỉ hiện thông tin cho đoạn/chương được chọn: issue → trích dẫn nguồn → gợi ý → hành động. Glossary/character suggestion phải có trạng thái đề xuất/đã duyệt và phạm vi áp dụng.
- Draft giữ được khi đổi inspector; navigation có thông báo nếu còn chưa lưu. Conflict hiện bản đang sửa và revision server, không tự ghi đè. Bulk replace có preview diff, số chương ảnh hưởng và khả năng hủy trước apply.
- Phím tắt đề xuất: Ctrl+Enter lưu đoạn và đến đoạn tiếp; Alt+Up/Down đổi đoạn; tìm chương không giành Ctrl+F khi đang đọc. Không tự submit trong IME composition tiếng Trung/Nhật/Hàn. Danh sách phím tắt là nơi tra cứu; mọi thao tác có nút tương đương.
- Settings tách AI Providers, Models, Translation, TTS, Storage, Appearance, Advanced. Key masked mặc định; thêm/thay/xóa key có kết quả kết nối, thời điểm kiểm tra và lỗi có hành động. Validate kết nối không được trình bày thành miễn phí hoặc cam kết dịch thành công.
- Model picker theo Provider → Model, tìm/lọc local/cloud, giá miễn phí/trả phí/chưa biết, context limit, capability. `Fast`, `Quality`, `Translation Optimized` chỉ được gắn là kết quả benchmark khi có dataset, model version và ngày chạy; lời đề xuất biên tập phải ghi rõ là curated recommendation. Giá chưa biết không phải 0.

## 7. Voice picker và audio

Voice row gồm tên, mô tả có nguồn metadata, locale, engine readiness; thiếu giới tính/phong cách thì không tự suy ra. **Đang phát** và **đã chọn** là hai trạng thái độc lập. Nghe thử không đổi preset chương. Chỉ một player phát tại một thời điểm; chọn giọng khác dừng sample trước; không autoplay khi mở trang.

Preview flow: sample text có giới hạn và đếm ký tự → generate → queued/running/cancel requested/failed/ready → URL artifact local → play/pause/seek → select. A/B dùng cùng văn bản và cùng các parameter hỗ trợ, có thể đổi qua lại tại cùng mốc; không khẳng định 420 ký tự luôn tương đương 20–60 giây. Cache key phải bao gồm engine/model/voice revision, normalized text và parameter thực ảnh hưởng âm thanh.

UI chỉ hiển thị speed/pitch/emotion/pause nếu adapter khai báo hỗ trợ và đã kiểm tra engine tương ứng. Nếu speed là hậu xử lý hoặc playback speed thì ghi rõ; không đổi tên nó thành engine-native control. Cần phân biệt tải model, warm-up, generate preview và render chương để không báo một spinner vô thời hạn.

Chương dài có progress theo segment và bước merge; audio phát bằng URL/range từ backend, không cần nạp cả file vào React state. “Hủy” có thể là yêu cầu dừng ở điểm an toàn, UI phải giữ trạng thái chờ dừng đến khi worker xác nhận. Retry chọn failed segment theo revision; giữ audio cũ đến khi artifact mới ready và không nhầm status “có file” với “đã nghe/đã duyệt”.

## 8. Design system ở mức nghiên cứu

Một bộ token cho toàn app; ưu tiên nền trung tính, một accent điều hướng, màu semantic riêng cho thành công/cảnh báo/lỗi. Màu semantic không bị giới hạn bởi quy tắc một accent marketing. Không chọn palette cuối khi chưa đo contrast và xem văn bản CJK/Vietnamese.

| Nhóm | Proposal để spec cụ thể hóa | Điều cần kiểm tra |
|---|---|---|
| Colors | `background`, `surface`, `surfaceRaised`, `border`, `text`, `textMuted`, `accent`, `success`, `warning`, `danger`, `focus` cho light/dark | Mỗi trạng thái có cặp foreground/background; không dùng màu làm tín hiệu duy nhất |
| Typography | UI system sans (Segoe UI phù hợp Windows), reading có fallback CJK/Vietnamese, mono chỉ mã/chi phí/diagnostics; UI 14–16px, reading 18–20px là giá trị prototype | Dấu tiếng Việt, line wrapping CJK, zoom 200%, line-height khoảng 1.6–1.8 cho reading |
| Spacing | Scale 4/8/12/16/24/32, khoảng cách theo nhóm thao tác, border ít | Không card cho từng mẩu metadata; phân đoạn bằng whitespace và trạng thái chọn |
| Button/input/select | Primary/secondary/destructive, native semantic khi đủ | Hover/active/focus/disabled/loading/error; disabled có lý do đọc được |
| Combobox/model/provider picker | Dùng primitive có semantic/keyboard phù hợp hoặc native select khi ít mục | Active option khác selected option; Escape/Tab/popup behavior |
| Modal/drawer/tooltip/toast | Modal cho việc cần quyết định chặn; drawer cho inspector mobile; toast kết quả ngắn | Focus return, Escape, không giữ lỗi quan trọng chỉ trong toast/hover |
| Tabs/table/chapter navigator | Tabs có label, table cho danh sách có cột cần so sánh; tree chỉ cho hierarchy thật | Focus và virtualized item không mất khi scroll |
| Editor/player/progress | Component domain riêng, states rõ và stable IDs | IME/selection/undo, audio keyboard, stale artifact, progress unknown |

Các lựa chọn thư viện cần spike sau khi duyệt: A native controls + CSS token có ít dependency; B accessible primitives + CSS token giúp component phức tạp; C full enterprise component suite có nhiều pattern nhưng cần cân nhắc bundle và custom editor. Repo hiện dùng React 19/React Router 7/Vite, chưa có CSS framework/design-system package trong `frontend/package.json`; không cần chuyển Next.js chỉ vì skill có default stack.

## 9. Accessibility và responsive acceptance đề xuất

Mục tiêu implementation là WCAG 2.2 AA cho các flow chính; chưa tuyên bố hiện tại tuân thủ.

- Text thường contrast ≥4.5:1, text lớn ≥3:1; kiểm tra theo cặp màu thực, cả hai theme. [W3C SC 1.4.3](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).
- Control pointer tối thiểu 24×24 CSS px hoặc thỏa ngoại lệ khoảng cách của chuẩn; đề xuất touch 44px như lựa chọn sản phẩm, không gọi là bắt buộc AA. [W3C SC 2.5.8](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html).
- Focus nhìn thấy, không bị job footer che hoàn toàn; drawer có focus return và không keyboard trap. [W3C SC 2.4.11](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum).
- Loading, save, queued, failed có status programmatic; không giành focus mỗi progress tick. [W3C SC 4.1.3](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html).
- Reflow khi viewport 320 CSS px, không kéo ngang toàn trang để đọc nội dung cơ bản. Desktop hai cột chỉ khi đủ chiều rộng; mobile đổi Source/Translation bằng tabs, chapter nav và inspector thành drawers riêng. [W3C SC 1.4.10](https://www.w3.org/WAI/WCAG22/Understanding/reflow).
- Kiểm tra keyboard-only, Windows screen reader, reduced motion, IME và contrast; automation accessibility chỉ hỗ trợ, không thay manual flow test.

## 10. Huge novel: giả thuyết và kế hoạch đo

Không có baseline performance được đo trong phiên này. Target sau đây là tiêu chí thử nghiệm đề xuất, cần hiệu chỉnh bằng máy 8 GB và dataset được duyệt:

| Kịch bản | Mục tiêu prototype | Cách đo |
|---|---|---|
| Library 1.000 rồi 10.000 chapters | Không tải toàn văn mọi chương; first page ≤100 summaries; DOM list bounded | Network payload, React/browser profiler, heap snapshot |
| Chapter 500–2.000 segments | Typing/đổi đoạn không bị long task lặp >50ms; chỉ editor active giữ state input nóng | Trace khi IME, filter QA, save; đo trên hardware thật |
| Đổi chapter đã cache | Phản hồi tương tác p95 <200ms; thời gian fetch hiển thị riêng | 30 lần với cache warm và cold tách biệt |
| Job stream dài | Không tích lũy event không giới hạn; history lấy theo cursor từ server | Chạy stream tổng hợp, reconnect, đo heap 30 phút |
| Audio chương dài | Không buộc download toàn file trước khi seek/play | HTTP range/network + playback thật |
| Review có filter và virtualization | Focus/selection/IME/undo vẫn đúng khi item ngoài viewport | Keyboard/E2E trên fixture lớn |

Không chọn virtualization chỉ để đạt số DOM thấp nếu làm hỏng việc tìm văn bản, chọn/copy đoạn hoặc screen reader. Có thể dùng chapter paging, lazy section và editor một đoạn active trước; chỉ thêm virtualization sau benchmark xác định bottleneck. Giữ batch giới hạn hiện có cho đến khi backend/resource budget chứng minh có thể thay.

## 11. Decision point và kiểm chứng tiếp theo

**QUESTION UX-Q01**

- Context: Workspace chương là trung tâm; A/B/C thay đổi cách navigation và phạm vi frontend rewrite.
- Options: A màn theo bước; B workspace chương + inspector; C nhiều tab/dockable panels.
- Recommended: B.
- Reason: Giữ ngữ cảnh sửa truyện dài, vẫn kiểm soát độ phức tạp và bộ nhớ.
- Impact: Coding model cần quyết định này trước khi khóa routes, workspace state và bộ acceptance UI. Các phần backend đọc-only research không phụ thuộc lựa chọn tiếp tục được.

Sau khi duyệt hướng: dựng prototype phi-production, test 5 nhiệm vụ (import, đổi provider, sửa/duyệt đoạn lỗi, preview/chọn giọng, nghe/xuất chương), đo completion time/lỗi thao tác và kiểm tra viewport 1366×768, 1024px, 390px, 320 CSS px. Không dùng screenshot mock để khẳng định live API/player hoạt động. Chỉ đánh dấu flow hoàn tất khi route thật, backend, job lifecycle và artifact playback/export được kiểm chứng cùng nhau.
