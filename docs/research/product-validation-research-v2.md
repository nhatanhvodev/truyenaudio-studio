# Product validation research v2 — workflow dịch truyện đến audio

Ngày kiểm tra: **07/09/2026**  
Phạm vi: kiểm chứng product workflow **import → translation → review → voice → audio → export**, ranh giới quyền xử lý/xuất bản, UX chọn provider/model, single narrator mặc định và multi-voice opt-in.  
Trạng thái: **research và kế hoạch kiểm chứng; chưa khóa architecture/spec và chưa thực hiện implementation**.

## 1. Kết luận điều hành

### 1.1 Phán định sản phẩm

**[FACT]** Codebase có đủ domain primitives cho một vòng đời chương: `ChapterState` đi từ `IMPORTED`, `TRANSLATION_REVIEW`, `TRANSLATION_APPROVED`, `VOICE_CONFIGURED`, `AUDIO_REVIEW`, `READY_TO_EXPORT` đến `EXPORTED`; có revision/hash, QA issue, voice plan, artifact và rights gate. Bằng chứng: `backend/app/contracts.py`, `backend/app/modules/projects/state_machine.py`, `backend/app/modules/speech/workflow.py` và `backend/app/modules/compliance/rights.py`.

**[FACT]** Luồng mà người dùng nhìn thấy chưa phải một trải nghiệm end-to-end đã được chứng minh. Route chính là nhiều màn hình nhỏ trong `frontend/src/routes/router.tsx`: nhập, dịch/hiệu đính, voice, audio, export. Route audio chỉ hiển thị hash và nút duyệt; component `frontend/src/features/audio/AudioReview.tsx` có logic review nhưng không được route chính sử dụng và không có player trong màn hình route.

**[INFERENCE]** Giá trị sản phẩm hiện tại nên được mô tả là “studio local để chuẩn bị và kiểm duyệt nội dung, sau đó xuất thủ công”, không phải “dịch và dựng audiobook tự động một click”. Tuyên bố này phù hợp với README: app bind loopback, không auto-upload và ưu tiên local control/audit.

### 1.2 Hướng kiểm chứng nên ưu tiên

**[INFERENCE]** Release đầu nên tối ưu một người dùng xử lý truyện dài trên máy local, với các nguyên tắc:

1. import luôn có preview và mapping rõ trước khi tạo chapter;
2. translation luôn giữ source/target, revision, QA và thao tác sửa/duyệt trong cùng ngữ cảnh;
3. **single narrator là mặc định** để giảm quyết định và chi phí render;
4. multi-voice chỉ xuất hiện khi người dùng chủ động bật, có narrator bắt buộc và assignment chưa giải quyết phải hiển thị rõ;
5. audio phải có artifact player thật trước khi cho duyệt;
6. public export chỉ khả dụng sau rights gate; private archive là đường lưu trữ cục bộ riêng;
7. provider/model chọn theo một profile hiển thị rõ cloud/local, capability, chi phí và trạng thái consent.

**[GAP]** Chưa có usability test, visual QA, benchmark ZH→VI, benchmark audio, test trên viewport thực hoặc live provider/TTS smoke trong phiên này. Vì vậy các kết luận về “dễ dùng”, chất lượng bản dịch, latency và mức hoàn thành sản phẩm chỉ là giả thuyết cần kiểm chứng.

## 2. Phương pháp và cách đọc bằng chứng

- Đọc prompt nghiên cứu tại `docs/operations/Prompt cho Codex — Research, Spec & Plan nâng cấp dự án dịch truyện.md`, README, các báo cáo `current-system-analysis`, `translation-engine-research`, `ui-ux-research`, rồi đối chiếu routes, feature components, backend APIs/workflows và tests.
- Root không có `.codegraph/`, nên không dùng CodeGraph theo `AGENTS.md`. `rg.exe` trên WinGet không chạy được; dùng `Select-String`, `git ls-files` và đọc các phạm vi dòng liên quan.
- Không chạy app, migration, benchmark, E2E, model download, cloud call hoặc TTS trả phí. Không đọc secret và không sửa source.

Nhãn trong báo cáo:

- **[FACT]** thấy trực tiếp trong source/docs hoặc được nguồn ngoài nêu rõ.
- **[INFERENCE]** suy luận sản phẩm từ các fact; cần kiểm chứng bằng prototype/usability test/benchmark.
- **[GAP]** bằng chứng hoặc khả năng hiện còn thiếu.
- **[DECISION]** câu hỏi cần chủ sở hữu sản phẩm/coding model quyết định trước khi khóa spec.

## 3. Product framing, persona và jobs-to-be-done

### 3.1 Persona chính: người vận hành studio cá nhân

**Bối cảnh:** một người nhập truyện tiếng Trung từ nguồn mà họ có quyền xử lý, dịch sang tiếng Việt, đọc lại các đoạn có cảnh báo, dựng audio tiếng Việt và tự upload gói đã xuất lên sản phẩm chính. Người này có máy Windows tài nguyên hạn chế, cần kiểm soát dữ liệu và chi phí.

**Mục tiêu:** hoàn thành từng chương có thể truy vết từ source revision đến translation revision, audio artifact và export manifest.

**Nỗi đau cần kiểm chứng:**

- không biết chương đã ở trạng thái nào hoặc nút “tiếp tục” còn thiếu điều kiện gì;
- phải nhập ID consent/budget bằng tay;
- không biết model/provider nào đang thật sự được dùng sau fallback;
- sửa bản dịch xong không biết audio cũ còn khớp revision nào;
- bấm chọn voice nhưng chưa nghe được sample/preview thực;
- khó phân biệt demo/fake, local chưa verify và cloud thật;
- quyền “được gửi lên cloud” bị nhầm với quyền “được xuất bản”.

### 3.2 Persona phụ dùng cho test

1. **Biên tập viên ngôn ngữ:** ưu tiên đối chiếu source-target, glossary, character memory, QA evidence, undo/conflict và thao tác bàn phím.
2. **Người vận hành thận trọng về quyền riêng tư/chi phí:** ưu tiên local-first, credential không nằm trong browser storage, preview quote, consent theo provider và fail closed.
3. **Người dùng power-user nhiều chương:** ưu tiên queue có resume, chapter navigator, lọc chương, retry segment và không mất draft khi chuyển chương.

Đây là persona phục vụ test và thiết kế; không suy ra app cần multi-user, role-based access hoặc public server.

### 3.3 Jobs-to-be-done

| Job | Kết quả người dùng cần | Tín hiệu hoàn thành cần có |
|---|---|---|
| Nạp truyện an toàn | Biết source nào, chapter nào, encoding/duplicate nào trước khi nhập | Preview có title/ordinal/text/warning; xác nhận là hành động riêng |
| Chọn cách dịch phù hợp | Chọn local/cloud, provider → model → profile mà hiểu trade-off | Profile, capability, context, giá/quota/consent và actual model được hiển thị |
| Giữ văn phong truyện | Dịch theo đoạn có ngữ cảnh, thuật ngữ và nhân vật nhất quán | Source-target cùng segment, context provenance, glossary/character suggestions, revision |
| Làm sạch trước audio | Không đẩy lỗi QA nghiêm trọng sang giọng đọc ngoài ý muốn | Issue evidence, sửa/ignore có lý do, approval hash và stale conflict |
| Chọn giọng tự tin | Nghe cùng một sample và preview text của mình trước khi render | Sample/preview artifact thật, trạng thái loading/failed/ready và preset đã chọn tách biệt |
| Render có thể tiếp tục | Chương dài không mất toàn bộ khi một segment lỗi | Progress segment/merge, retry segment theo revision, artifact cũ giữ đến khi mới ready |
| Xuất đúng phạm vi | Có private archive hoặc publication bundle phù hợp rights | Gate reasons dễ hiểu, evidence/hash, manifest/checksum và destination rõ |

## 4. Kiểm chứng workflow hiện tại

### 4.1 Ma trận route → backend → trạng thái sản phẩm

| Bước | Bằng chứng hiện tại | Điều người dùng thấy | Khoảng trống product validation |
|---|---|---|---|
| Tạo project | `ProjectWizard`; API project | Form tạo project | Chưa kiểm chứng empty state, ngôn ngữ, rights/source onboarding và phục hồi khi rời trang |
| Import file/folder | `ImportScreen` gọi `/api/projects/{id}/chapters/import/preview`; `ImportPreview` hiển thị candidate/warning rồi confirm | Có preview và confirm mapping | Không visual/runtime test; preview không phải lúc nào cũng kiểm tra quyền nguồn; Wenku path lưu draft vào `localStorage` và cắt text mỗi chapter ở 4.000 ký tự |
| Import Wenku | `WenkuImport` gọi `/api/wenku/info`, `/api/wenku/preview`, rồi import chapters; README nói không bypass CAPTCHA/DRM/login | Chọn URL/ID, BXH, range chapter, xem progress ước tính | Cần kiểm chứng legal source messaging, preview lock/VIP, cancel/retry, range lớn và không mất draft |
| Configure translation | Route hiện nhấn mạnh Google AI Studio Gemini; có fake convert, Qwen và custom model; Qwen nhập `cloudConsentId`/`budgetAuthorizationId` bằng tay | Người dùng nhìn thấy nhiều card/provider khác nhau | Chưa có provider → model → profile flow thống nhất; key Gemini lưu `localStorage`; claims “mượt mà/free” chưa có benchmark; key/consent UX phải chuyển thành trạng thái có nguồn |
| Translate | Backend có `TranslationWorkflow`, cache và QA; nhưng `enqueue_translation()` hiện thực hiện inference đồng bộ trong request theo current-system-analysis | Nút “dịch toàn bộ chương”, busy state | Không có bằng chứng queue production launcher xử lý thật; timeout/cancel/resume và chapter dài chưa được kiểm chứng |
| Review/edit | Route hiển thị segments, filter ALL/ISSUES/BLOCKERS, sửa từng segment; approval gửi run hash; test có stale conflict | QA evidence và nút sửa/duyệt | Force approval có thể dismiss toàn bộ blocker, kể cả critical; chưa có bulk diff/undo/character/glossary inspector; số đoạn hiển thị thay đổi theo filter |
| Configure voice | `VoiceScreen` gọi `/api/voices?locale=vi-VN`, chọn voice active/available đầu tiên rồi `configure-single` | Nút “Render một giọng” | Không có sample/preview trong route; chưa cho chủ động chọn từ catalog thật; “available” phụ thuộc model/license/hash/POC; single narrator mặc định chưa có product copy/gate rõ |
| Multi-voice | API `/api/voice-plans/multi`, role assignment component và `/multivoice-cloud-demo` tồn tại; README gọi assisted multi-voice | Demo riêng cho consent/role/selective rerender | Chưa phải production one-click; cần opt-in, narrator bắt buộc, role unresolved, giới hạn 4 role và chi phí hiển thị trong flow chính |
| Render audio | `POST /audio/render`; `SpeechWorkflow` có segment cache/master/SRT, TTS adapter và fake mode | Chuyển sang Audio khi render trả kết quả | Route chính không có player/seek/segment issue; audio route có thể đọc artifact status mới nhất và không chứng minh đó là revision người dùng vừa duyệt; worker wiring chưa chứng minh launcher production |
| Review audio | `AudioReview` component có Render/Approve và ASR advisory nhưng không nối route chính | Route chính chỉ hiện master hash và “Phê duyệt audio” | Không thể xác nhận bằng product test rằng người dùng đã nghe; thiếu stale artifact, segment-level retry, playback failure và “approved vs ready” distinction |
| Export | `/exports/gate`, `/private`, `/publication`; `ExportGate` hiển thị allowed/reasons/hash; publication request trong route hard-code “Tập 1”, number 1, premium false | Private archive có thể tạo; public bị disable khi gate block | Metadata phải lấy từ project/chapter và cho review; đường tải file/manifest chưa được product-test; cần nói rõ manual upload boundary |

### 4.2 Flow trạng thái được đề xuất để test

```text
Nháp project
  → Preview source
  → Xác nhận import
  → Preflight translation (local/cloud + profile + cost/consent)
  → Translation job
  → Review revision + QA
  → Approve translation
  → Chọn narrator mặc định
  → Sample + preview text
  → Configure audio / render segments
  → Nghe master + xử lý issue
  → Approve audio
  → Rights/export gate
  → Private archive hoặc publication bundle
  → Upload thủ công sang sản phẩm chính
```

**[INFERENCE]** UI nên coi đây là một state machine có “reason to continue” cho từng chapter. Nút điều hướng tới Audio không được ngầm hiểu bản dịch đã được duyệt; backend gate vẫn là nguồn sự thật.

## 5. Source/rights boundary và thông điệp sản phẩm

### 5.1 Fact từ domain

**[FACT]** `SourceType` phân biệt `SELF_AUTHORED`, `PUBLIC_DOMAIN`, `OPEN_LICENSE`, `LICENSED_PARTNER`, `USER_SUPPLIED_PRIVATE`, `UNKNOWN`. `RightsScope` phân biệt `TRANSLATE_VI`, `CREATE_AUDIO`, `PUBLIC_STREAM`, `DOWNLOAD`, `MONETIZE`; `RightsStatus` có `PRIVATE_ONLY`, `REVIEW_REQUIRED`, `CLEARED`, `EXPIRED`, `BLOCKED`.

**[FACT]** `RightsGate` cho private archive không yêu cầu scope; publication mặc định yêu cầu `TRANSLATE_VI`, `CREATE_AUDIO`, `PUBLIC_STREAM`, thêm `DOWNLOAD` nếu include download và `MONETIZE` nếu metadata premium. Gate trả reasons và `rights_evaluation_hash`.

**[FACT]** `CloudCallGuard` yêu cầu provider profile enabled, consent `GRANTED`, policy snapshot ready và không stale, quyền cho AI/third-party cloud, quota/rate card/budget phù hợp. Với private user-supplied source, attestation evidence là điều kiện riêng cho cloud.

### 5.2 Ranh giới phải được nói bằng ngôn ngữ người dùng

**[INFERENCE]** Onboarding và mỗi preflight nên tách ba câu hỏi:

1. “Bạn có quyền xử lý/biên tập source này không?”
2. “Bạn có cho phép gửi dữ liệu sang provider cloud này không?”
3. “Bạn có quyền tạo audio/phát hành/tải xuống/kiếm tiền ở territory này không?”

Consent cloud chỉ trả lời câu 2. Nó không cấp quyền cho câu 1 hoặc câu 3.

**[GAP]** Frontend có `RightsEditor` và `CloudConsent` component nhưng route chính chưa cho thấy một onboarding rights flow hoàn chỉnh; người dùng có thể gặp gate reason trễ, sau khi đã tốn công dịch/render. Đây là gap product flow cần test, không tự kết luận backend gate sai.

**Acceptance cần có:**

- trước cloud translation/TTS, preflight hiển thị provider, model, region, dữ liệu gửi, retention/policy snapshot, estimated usage/cost và consent status;
- trước publication, hiển thị từng scope thiếu, territory, evidence đang dùng, thời hạn và hành động sửa;
- private archive ghi rõ “lưu dùng riêng” và không tự chuyển thành public;
- không hiển thị claim “miễn phí” khi giá/quota chưa biết; không coi consent hoặc API key là proof of rights;
- source VIP/locked/unknown phải có cảnh báo và không tự hứa crawl/publication.

## 6. Voice product validation

### 6.1 Single narrator mặc định

**[FACT]** `VoiceMode` có `SINGLE_NARRATOR` và `ASSISTED_MULTI_VOICE`. `configure_single()` tạo voice plan narrator; `VoiceCatalog` ưu tiên preset `vieneu`, sau đó `piper`, nhưng chỉ coi voice available khi model/license/hash/POC conditions hợp lệ. `VoiceScreen` hiện tự chọn preset available đầu tiên.

**[INFERENCE]** Single narrator phải là default product path vì người dùng có mục tiêu dựng chương hoàn chỉnh, không phải gán diễn viên cho từng câu. Default này giảm số quyết định, làm cache/retry đơn giản hơn và phù hợp máy local một worker. UI cần cho đổi narrator rõ ràng, nhưng không mở role assignment ngay trong happy path.

### 6.2 Multi-voice opt-in

**[INFERENCE]** Multi-voice chỉ mở sau một hành động chủ động như “Bật nhiều giọng cho chương này”. Khi bật:

- narrator vẫn bắt buộc và là fallback cho đoạn chưa gán;
- tối đa 4 role theo component hiện có (narrator + 3 role phụ);
- unresolved assignment phải là trạng thái hiển thị, không âm thầm chọn role;
- preview A/B dùng cùng text và parameter;
- chi phí/thời gian ước tính phải tính theo số voice/segment;
- thay đổi role sau render làm stale những segment liên quan, giữ artifact cũ đến khi artifact mới ready;
- consent/budget cloud áp dụng theo provider và operation, không suy ra từ việc đã chọn voice.

**[GAP]** Demo `MultiVoiceCloudDemo` kiểm tra UX/logic nhưng không chứng minh route production tạo plan, render, playback và export multi-voice end-to-end. Acceptance phải gọi rõ demo/fake là evidence UI logic, không phải provider/TTS PASS.

### 6.3 Voice picker acceptance

**[INFERENCE]** Mỗi voice row cần hiển thị name, provider/model, locale, local/cloud, verified/readiness, license/cost tier nếu có nguồn; thiếu metadata gender/style thì hiển thị “chưa cung cấp” thay vì suy đoán. “Đang phát” và “đã chọn” là hai trạng thái độc lập; không autoplay.

Preview flow tối thiểu:

```text
Chọn voice → sample text có giới hạn → tạo preview artifact
→ queued/running/failed/ready → play/pause/seek
→ nghe preview văn bản của người dùng → xác nhận preset
```

**[GAP]** `POST /api/voices/preview` hiện trả một `JobView` trạng thái `QUEUED` và cache key nhưng chưa chứng minh route/player nhận artifact ready. Cần test cả loading lâu, unavailable model/license, retry, chỉ một player phát và cache key thay đổi khi text/voice/settings đổi.

## 7. Provider/model choice UX

### 7.1 Vấn đề product hiện tại

**[FACT]** Route translation có card Gemini nổi bật, lưu API key Gemini vào `localStorage`, select preset/custom model và hướng dẫn lấy key. Qwen dùng các input `Cloud consent ID` và `Budget authorization ID`; fake converter ở cùng khu vực. `ProviderSettings`, `CloudConsent` và model/voice feature components hiện thiên về isolated component tests hơn là một settings flow được nối qua route chính.

**[INFERENCE]** Cách trình bày này khiến provider marketing, demo, profile thật và model selection trộn trong một màn hình. Người dùng dễ hiểu “nút đang sáng” là provider đã sẵn sàng, dù backend còn cần consent, policy, rights, quota và budget.

### 7.2 Mô hình mental model nên test

```text
Provider profile
  ├─ Provider kind: translator / reviewer / TTS
  ├─ Credential reference và connection status
  ├─ Region / policy / quota / rate card
  └─ Models
       ├─ capability + context
       ├─ local/cloud + free/paid/unknown
       ├─ curated recommendation hoặc benchmark evidence
       └─ actual model/version của run
```

**[INFERENCE]** UI có thể hiển thị dạng Provider → Model → Translation profile. Người dùng không cần gõ ID nội bộ; backend tạo và trả về status/reference. `Free`, `Fast`, `Quality`, `Translation optimized` chỉ được xem là label có bằng chứng nếu có dataset, version, ngày benchmark; nếu là lựa chọn biên tập thì ghi “đề xuất tuyển chọn”.

### 7.3 Acceptance cho provider/model picker

- Chọn local không mở consent cloud và không gọi network ngoài ý muốn.
- Chọn cloud mở preflight với dữ liệu, region, policy, quota, estimate/cost và rights.
- Model unavailable/context overflow/timeout/rate limit có reason cụ thể và fallback policy rõ.
- Run lưu planned provider/model và actual provider/model/version/usage; nếu fallback xảy ra phải hiện trong history.
- Key không nằm trong URL, `localStorage`, Redux state export, log hoặc error message; UI chỉ dùng credential reference masked.
- “Validate connection” chỉ chứng minh kết nối/capability tại thời điểm kiểm tra, không tuyên bố chất lượng dịch hay miễn phí.

## 8. Research insight cho translation/review workspace

### 8.1 Bằng chứng ngoài

- W3C APG nêu combobox có popup và phân biệt select-only với editable; keyboard model yêu cầu quản lý `Tab`, `Down Arrow`, `Escape`, `Enter` và accessible name/value. APG là nguồn hướng dẫn informative, không phải chuẩn normative; implementation vẫn phải đối chiếu WCAG/ARIA.
- OmegaT mô tả editor chia văn bản thành numbered segments, có các pane cho glossary, fuzzy matches, comments, properties và status. Đây là bằng chứng tham khảo cho việc đặt context cạnh segment, không phải bằng chứng rằng layout đó nhanh hơn trong app này.
- Weblate cho thấy translator cần context, nearby strings, glossary, history, comments, suggestions và quality checks cho placeholder/markup/punctuation. Weblate cũng nói quality checks không thay thế translator judgment.
- Karpinska & Iyyer, WMT 2023 (ACL), đánh giá người dịch trên 18 cặp ngôn ngữ và thấy dịch paragraph có context tốt hơn sentence-by-sentence ở nhiều chỉ số, nhưng vẫn có critical errors/omissions và cần human intervention để giữ author voice. Nghiên cứu này không chứng minh model hiện tại của studio tốt cho Chinese→Vietnamese.

### 8.2 Product implication

**[INFERENCE]** Workspace nên mặc định căn source/target theo segment ID, có mode đọc liên tục, inspector cho QA/glossary/character/context và lưu draft theo revision. Khi filter issue, ordinal source không được đổi thành số thứ tự mới khiến người dùng mất vị trí.

**[INFERENCE]** Gợi ý dịch, glossary và repair phải có nguồn/độ tin cậy/trạng thái “đề xuất”; không tự overwrite translation. Bulk replace cần preview diff, số chương/đoạn bị ảnh hưởng và nút hủy.

**[GAP]** Chưa có test IME Trung/Nhật/Hàn, copy/select trên chapter dài, screen reader, focus return, zoom 200%, CJK/Vietnamese line wrapping hoặc conflict UX trong route chính.

## 9. Các option release và trade-off

Các option sau là framing cho product decision; không khóa architecture.

| Tiêu chí | A — Local-first tối giản | B — Balanced workflow (khuyến nghị để prototype) | C — Studio mở rộng |
|---|---|---|---|
| Happy path | Import preview → fake/local translation → review → single narrator → private export | Import nhiều nguồn → provider/model profile → review QA → single narrator preview/player → audio approve → private/public gate | Cộng queue nhiều chapter, dynamic model discovery, multi-voice production, history/compare |
| Complexity | Thấp | Vừa | Cao |
| Value kiểm chứng nhanh | Cao cho one-user local | Cao nhất cho workflow mục tiêu | Thấp hơn vì nhiều nhánh |
| Cloud/provider | Có thể chưa bật hoặc chỉ adapter mock | Opt-in, profile/consent/budget rõ | Nhiều provider/fallback/circuit breaker |
| Audio | Một voice, render resumable cơ bản | Một voice + preview artifact + player + retry segment | Multi-voice timeline, batch audio, nhiều preset |
| Rights | Private archive rõ; publication gate cơ bản | Rights onboarding/preflight theo scope/territory/evidence | Policy/version/audit nhiều project và publication target |
| Rủi ro | Chưa trả lời nhu cầu provider/model | Cần wire nhiều state nhưng kiểm chứng được | Scope creep, RAM/worker/UX phức tạp |
| Điều kiện chuyển tiếp | Có completion baseline | Prototype pass các task và acceptance | Chỉ mở sau usage evidence từ B |

**Khuyến nghị nghiên cứu:** dùng B làm prototype validation target, giữ single narrator là đường mặc định và đặt multi-voice dưới opt-in. Đây là recommendation sản phẩm; chưa phải quyết định kiến trúc thay cho `upgrade-decision.md`.

## 10. Usability test plan chưa thực hiện

### 10.1 Mục tiêu

Kiểm tra người dùng có:

1. hiểu chapter đang ở trạng thái nào;
2. import đúng mà không bỏ qua warning;
3. chọn provider/model và biết dữ liệu/chi phí/risk;
4. sửa và approve đúng revision/QA;
5. chọn narrator sau khi nghe preview;
6. phân biệt audio ready, listened, approved và stale;
7. hiểu private archive khác publication bundle;
8. hoàn thành flow bằng bàn phím và ở mobile-width tối thiểu.

### 10.2 Đối tượng và fixture

- 5–7 người dùng có kinh nghiệm dịch/biên tập truyện hoặc audiobook; ít nhất 2 người không quen code/ID kỹ thuật.
- Một fixture private hợp pháp 3 chapter: một chapter sạch, một chapter có missing ordinal/empty candidate, một chapter có QA blocker và một đoạn dài cần context.
- Hai provider profile giả lập: local ready và cloud cần consent/budget; một model context không đủ để kiểm tra error.
- Hai voice local: một ready, một thiếu model/license; một preview artifact ready và một preview fail.
- Rights fixture: private-only, cloud-allowed, publication-cleared, expired/blocked.

### 10.3 Nhiệm vụ test

| ID | Nhiệm vụ | Success condition |
|---|---|---|
| T1 | Tạo project và import EPUB/folder, xử lý một warning trước confirm | Người dùng xem preview, sửa mapping hoặc loại candidate lỗi, chỉ confirm sau khi hiểu warning |
| T2 | Dịch chapter bằng local profile; sau đó thử cloud profile | Người dùng tìm được Provider → Model, biết local/cloud và không phải gõ internal ID; cloud không chạy trước consent/preflight |
| T3 | Lọc QA blocker, sửa một segment và approve revision | Sửa đúng segment, thấy saved revision/hash, hiểu blocker và không bị stale overwrite |
| T4 | Chọn narrator, nghe sample và preview text của mình | Không nhầm playing với selected; không render chapter trước khi preview ready/confirm |
| T5 | Bật multi-voice theo chủ ý, gán một role, để một đoạn unresolved | Người dùng thấy narrator fallback/unresolved và biết multi-voice là opt-in; không tự giả định mọi đoạn đã gán |
| T6 | Nghe audio master, gặp một audio/ASR advisory và approve | Có player thật hoặc test harness tương đương; người dùng phân biệt advisory với blocker và approve artifact đúng revision |
| T7 | Xuất private archive rồi thử publication khi rights thiếu | Private path vẫn rõ; publication bị chặn với reason/scope/evidence/action cụ thể |

### 10.4 Metrics và ngưỡng prototype

Đo mỗi task: completion (pass/fail), thời gian, số lần backtrack, lỗi chọn nhầm provider/model/voice, số lần bỏ qua warning, câu hỏi hỗ trợ, confidence tự đánh giá 1–5. Ghi screen/console/network state chỉ khi người tham gia đồng ý; không ghi secret.

**Ngưỡng đề xuất để tiếp tục:**

- ≥80% người test hoàn thành T1, T3, T4, T7 mà không cần hướng dẫn;
- 0 người test gửi cloud khi consent/rights preflight đang block;
- 0 người test tưởng “audio ready” đồng nghĩa “đã nghe/đã approve”;
- ≥80% phân biệt single narrator và multi-voice opt-in;
- mọi completion được lặp lại trên fixture có stale hash hoặc model unavailable;
- không có lỗi P0 về mất draft, leak credential hoặc publication khi gate block.

Các ngưỡng là heuristic để quyết định prototype, không phải benchmark khoa học. Cần ghi sample size và điều kiện test khi báo cáo.

### 10.5 Accessibility/responsive checks

- Keyboard-only: Tab order, Enter/Escape, combobox active vs selected, focus return từ inspector/modal, không tự submit trong IME composition.
- Screen reader: accessible name/value cho provider/model/voice, `role=status` cho queued/running/ready/error, reason của disabled button.
- Viewport 1366×768, 1024px, 390px và 320 CSS px; source/target chuyển sang tabs/drawers khi không đủ chiều rộng.
- Contrast theo cặp màu thực, focus không bị job footer che, zoom 200%, reduced motion.

W3C APG nên được dùng làm interaction reference; APG chính thức nhắc rằng APG informative, còn WCAG/ARIA là normative. Không ghi “WCAG compliant” chỉ từ component test.

## 11. Decision questions

### [DECISION] PV-Q01 — Scope prototype

- **A:** chỉ local/fake + single narrator + private archive;
- **B (khuyến nghị):** Balanced provider/profile + rights preflight + review + single narrator preview/player + private/public gate;
- **C:** thêm production multi-voice, batch nhiều chapter, dynamic model discovery và fallback đầy đủ.

Question: release đầu cần chứng minh giá trị nào trước — local reliability, end-to-end balanced workflow hay scale/power-user?

### [DECISION] PV-Q02 — Audio gate

- **A:** cho approve khi master artifact ready;
- **B (khuyến nghị):** yêu cầu audio player/metadata và trạng thái “đã nghe” tùy chọn trước approve;
- **C:** bắt buộc nghe/ASR sign-off từng segment theo profile chất lượng.

Khuyến nghị B vì tránh tuyên bố người dùng đã kiểm tra âm thanh nhưng chưa làm cho ASR thành gate cứng.

### [DECISION] PV-Q03 — Multi-voice exposure

- **A:** ẩn khỏi release đầu;
- **B (khuyến nghị):** opt-in per chapter, narrator bắt buộc, tối đa 3 role phụ, demo label rõ;
- **C:** multi-voice là flow mặc định.

Khuyến nghị B để bảo toàn nền tảng assisted multi-voice mà không làm tăng cognitive load happy path.

### [DECISION] PV-Q04 — Provider UX

- **A:** curated provider/model list;
- **B (khuyến nghị):** curated recommendations + dynamic models khi metadata đủ;
- **C:** dynamic discovery/fallback tự động là mặc định.

Khuyến nghị B vì model quality/price và capability cần provenance; dynamic list không tự tạo ra quality evidence.

## 12. Acceptance gap register

| ID | Gap/risk | Impact | Evidence còn thiếu | Điều kiện chấp nhận đề xuất |
|---|---|---|---|---|
| PV-G01 | Route audio không có player và component AudioReview chưa nối | Người dùng không thể chứng minh đã nghe trước approve | Route E2E + artifact playback thực | Player có loading/error/seek; approval gửi đúng artifact hash/revision |
| PV-G02 | Voice route tự chọn preset đầu tiên | Chọn voice không có chủ ý; có thể render nhầm | Voice catalog test + user task | Danh sách selectable, sample/preview ready, selected tách playing |
| PV-G03 | Multi-voice production chưa one-click | README/demo có thể bị hiểu là production complete | API→worker→artifact→player→export test | Opt-in, narrator fallback, role limit, unresolved/retry/stale rõ |
| PV-G04 | Gemini key lưu browser; provider UI trộn demo/cloud | Credential/privacy và mental model sai | Threat model + browser storage check + user test | Credential reference backend, masked status, no key in storage/log/URL |
| PV-G05 | Qwen nhập consent/budget ID bằng tay | Lỗi thao tác và khó hiểu gate | Profile/consent integration test | Backend-issued reference, preflight có reason/action |
| PV-G06 | Translation request dài chạy đồng bộ; worker handler production chưa wire | UI busy/error/resume không đáng tin | Crash/timeout/cancel/restart test | Durable job, checkpoint, retry/failure state được hiển thị |
| PV-G07 | Force approval có thể dismiss major/critical blocker hàng loạt | Chất lượng và audit bị yếu | Review approval tests theo từng severity | Accept-risk có issue-level reason/actor/time; critical policy rõ |
| PV-G08 | Rights editor/onboarding chưa vào core route | Block trễ sau khi đã dịch/render | Rights fixture task T7 | Gate trước operation; scope/territory/evidence/expiry/action dễ hiểu |
| PV-G09 | Publication metadata hard-code “Tập 1” | Gói sai chapter/episode | Multi-chapter export test | Metadata domain-derived, review trước build, manifest khớp |
| PV-G10 | Cache/provenance fallback actual model chưa chắc được ghi | Không tái hiện chi phí/chất lượng | Fallback mocked integration | planned/actual provider/model/version/usage theo attempt |
| PV-G11 | Chapter/segment lớn chưa đo trên máy 8 GB | DOM/heap/latency có thể làm workflow unusable | Fixture 1.000/10.000 chapter, 500–2.000 segment | Paging/bounded rendering, no long task regression, data preserved |
| PV-G12 | Tests chủ yếu mock isolated components/demo | Green test không chứng minh product flow | Route-level E2E with backend fixtures | Mỗi critical transition route→API→artifact→next state được kiểm chứng |

## 13. Evidence và nguồn tham khảo

### 13.1 Nguồn trong repository

- [README.md](../../README.md) — product boundary, local-first, manual upload, fake/demo và current limitations.
- [current-system-analysis.md](current-system-analysis.md) — baseline 07/09/2026, module map, actual flow, technical debt và chưa visual/runtime QA.
- [translation-engine-research.md](translation-engine-research.md) — context, glossary, memory và document-level translation caveats.
- [ui-ux-research.md](ui-ux-research.md) — IA, workspace, accessibility, options A/B/C và proposed flow; chưa phải usability evidence.
- [frontend/src/routes/router.tsx](../../frontend/src/routes/router.tsx) — route wiring và current screen behavior.
- [backend/app/contracts.py](../../backend/app/contracts.py) — source/rights/chapter/job/voice/artifact state vocabulary.
- [backend/app/modules/compliance/rights.py](../../backend/app/modules/compliance/rights.py) — publication/private gate và rights evaluation hash.
- [backend/app/modules/compliance/cloud.py](../../backend/app/modules/compliance/cloud.py) — cloud consent/policy/rights/budget guard.
- [backend/app/api/voices.py](../../backend/app/api/voices.py), [backend/app/api/voice_plans.py](../../backend/app/api/voice_plans.py) — voice catalog/preview và assisted multi-voice API.
- [frontend/src/features/audio/AudioReview.tsx](../../frontend/src/features/audio/AudioReview.tsx), [frontend/src/features/voices/VoiceBrowser.tsx](../../frontend/src/features/voices/VoiceBrowser.tsx) — isolated audio/voice behavior; route integration còn là gap.

### 13.2 Nguồn chính thức ngoài repository

Ngày dưới đây là ngày truy cập trong phiên research: **07/09/2026**.

1. [W3C WAI-ARIA Authoring Practices — Introduction](https://www.w3.org/WAI/ARIA/apg/about/introduction/) — APG là informative; patterns mô tả purpose, keyboard model và semantics, nhưng không thay normative WCAG/ARIA. Truy cập 07/09/2026.
2. [W3C APG — Combobox Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/) — accessible name/value và keyboard behavior cho provider/model picker; Escape phải cho phép bỏ popup mà không đổi lựa chọn trước đó trong các pattern phù hợp. Truy cập 07/09/2026.
3. [W3C APG — Tree View Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/treeview/) — chỉ dùng tree khi chapter có hierarchy thật; phải tách focus và selection. Truy cập 07/09/2026.
4. [OmegaT Manual — Panes](https://omegat.sourceforge.io/manual-latest/en/chapter.panes.html) — editor numbered segments, glossary/context panes, notifications và layout có thể điều chỉnh. Truy cập 07/09/2026.
5. [Weblate Documentation 2026.9.1 — Translating](https://docs.weblate.org/en/latest/user/translating.html) — context, nearby strings, glossary, history, suggestions và checks cho placeholder/markup/punctuation; checks không thay translator judgment. Truy cập 07/09/2026.
6. [Karpinska & Iyyer, ACL/WMT 2023 — Large Language Models Effectively Leverage Document-level Context for Literary Translation, but Critical Errors Persist](https://aclanthology.org/2023.wmt-1.41/) — human evaluation 18 language pairs; paragraph context cải thiện nhiều chỉ số nhưng critical errors/omissions vẫn tồn tại và cần human intervention. Công bố 12/2023; truy cập 07/09/2026.

Các nguồn trên chỉ hỗ trợ interaction pattern, translation-context rationale và yêu cầu human review. Chúng không chứng minh UI đề xuất nhanh hơn, model hiện tại tốt hơn, hoặc provider/TTS runtime đã PASS.

## 14. Bằng chứng cần chạy sau khi chọn scope

1. Prototype route-level với fixture hợp pháp, không dùng production data.
2. Usability sessions T1–T7 và metrics ở mục 10.
3. Backend integration fixtures cho rights/consent/budget/model unavailable/timeout/fallback/stale hash.
4. Local voice preview artifact và chapter audio player; kiểm tra thực tế `play/pause/seek`, retry segment, stale revision và master metadata.
5. E2E từ import confirm đến private export/public gate; không đánh dấu complete khi chỉ có isolated feature test.
6. Benchmark nguồn 1.000/10.000 chapter và chapter 500–2.000 segment trên máy đích 8 GB; ghi rõ hardware, dataset, model version và ngày.
7. Accessibility manual check: keyboard, Windows screen reader, IME, contrast, reflow 320px, focus và reduced motion.
8. Chỉ sau khi các bằng chứng trên đạt ngưỡng mới khóa quyết định A/B/C, routes/state ownership và implementation tasks.

