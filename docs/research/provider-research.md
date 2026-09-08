# Nghiên cứu provider cho dịch truyện

Ngày khảo sát: **2026-09-07**. Trạng thái: **RESEARCH / OPTIONS — CHƯA KHÓA QUYẾT ĐỊNH SẢN PHẨM**.

Tài liệu dựa trên mã hiện tại và tài liệu chính thức đọc trong phiên này. Không gọi inference, không dùng key, không kiểm tra account/quota thực tế, không chạy benchmark, không cài dependency. Giá/quota là ảnh chụp tài liệu công khai, không phải cam kết của tài khoản người dùng. Mọi số tiền dưới đây tính bằng USD; giá token phải gắn model, region, tier, thời điểm và đơn vị.

## 1. Điểm xuất phát đã xác minh trong code

| Bằng chứng | Hiện trạng và tác động |
|---|---|
| `backend/app/contracts.py:228-247,323-325` | Đã có `TranslationRequest`, `TranslationResult`, `TranslatorAdapter`. Request chứa glossary, TM, domain và story memory; interface hiện chỉ `capabilities()` + `translate()`. Nên mở rộng boundary này thay vì thay toàn bộ domain bằng chat messages. |
| `backend/app/settings/config.py:11-14` | App cố định loopback, một worker, data trên D:. Cloud provider là lựa chọn inference trong app local, không đồng nghĩa chuyển app lên SaaS. |
| `backend/app/db/models.py:309-321,489-494,525-529` | Có `ProviderProfile`, bảng giá và ledger. Có nền tảng lưu model, region, config và secret reference; chưa có registry chuẩn hóa metadata/capability/account availability. |
| `backend/app/api/translation.py:79-132,207-287` | API phân nhánh `/fake`, `/qwen`, `/gemini`; construction adapter nằm trong API. Thêm mỗi provider theo cách này sẽ tăng nhánh route và lặp wiring. |
| `backend/app/providers/gemini_mt.py:34-53` | Đã fetch models nhưng bỏ context/pricing, không theo `nextPageToken`; non-200 trả `[]`, làm “không có model” lẫn “key/network lỗi”. |
| `backend/app/providers/gemini_mt.py:112-151` | Prompt Trung–Việt và generation config nằm ngay trong adapter; cap output 8.192 là cap app, không phải metadata chung của Gemini. |
| `backend/app/modules/translation/workflow.py:389-419,543-582` | Workflow nhận adapter nhưng tự chọn profile riêng; profile/model có thể khác adapter được route cung cấp. Cần resolve một execution plan trước run. |
| `backend/app/modules/translation/workflow.py:717-740` | Cache đã có source/provider/model/version/region/prompt/style/glossary/memory. Cần giữ thiết kế có phiên bản này và bổ sung cấu hình/đích dịch cùng actual model khi đổi adapter. |
| `backend/app/modules/jobs/retry.py:7-44` | Có retry bounded, delay 2/10/30 giây và xử lý Retry-After; chưa phải policy chung cho các fallback ẩn của Gemini. |

### Các phát hiện cần đưa vào backlog trước mở rộng provider

**PR-R01 — Critical: đường Gemini không đi qua cloud guard và budget guard thực.** `api/translation.py:130-131` tự thay ID thiếu bằng chuỗi `consent:gemini-user` / `budget:gemini-user`; `:283-287` không truyền guard cho adapter. `gemini_mt.py:98-110` chỉ kiểm tra khi guard/project/profile đều được truyền. Tác động: có thể gửi văn bản cloud mà không xác minh record consent/budget theo cơ chế Qwen hiện có. Đề xuất trong implementation sau: mọi cloud adapter nhận execution authorization đã resolve; kiểm thử route thiếu/thu hồi consent và không đủ ngân sách phải dừng trước HTTP. Không sửa trong phiên research.

**PR-R02 — High: Qwen wire contract không khớp API chính thức đã đọc.** `qwen_mt.py:61` chọn native DashScope generation endpoint; `:149-161` gửi `segments`, `source_language`, `target_language` ở top-level; `:106-112` chờ `translations[0].target_text`. Ví dụ chính thức hiện tại dùng messages và `translation_options.source_lang/target_lang`, native response `output.choices[].message.content`, hoặc OpenAI-compatible response `choices[].message.content`. Không có bằng chứng hợp đồng riêng cho shape đang dùng. Tác động dự kiến: request bị từ chối hoặc parser báo rỗng dù API trả thành công; fake transport không chứng minh tích hợp live. Cần fixture nguyên bản theo đúng endpoint/region đã chọn và test parser/usage/error; sau đó mới smoke nhỏ được cấp phép. [Alibaba Qwen-MT](https://www.alibabacloud.com/help/en/model-studio/machine-translation).

**PR-R03 — High: định dạng keyring writer/reader không tương thích.** `api/cloud_profiles.py:83-90` lưu bằng service `truyenaudio-studio`, username `provider-profile:<id>`, reference `keyring:provider-profile:<id>`. `providers/qwen_mt.py:33-39` tách reference theo `/` để lấy service/username; reference trên sẽ thành service sai và username rỗng. Tác động: profile do UI/API tạo có thể không đọc được key bằng Qwen reader. Cần một credential repository thống nhất; test round-trip với fake keyring, rồi kiểm tra Windows vault được phép. Không in key; không “sửa” bằng đưa secret vào SQLite.

**PR-R04 — High: fallback/chi phí/provenance không thống nhất.** `gemini_mt.py:154-207` dùng alias hard-code, thử tiếp cả sau network exception, có thể đổi sang Pro; guard nếu có chỉ chạy trước vòng lặp. `TranslationResult` trả actual model ở `:250-255`, nhưng workflow đã resolve model/cache trước call. Cần attempt ledger, model thực, budget trước từng dispatch và `BILLING_UNKNOWN` khi không biết request đã được xử lý; không coi transport timeout là chắc chắn chưa tính tiền.

## 2. Ma trận API và capability

“Có” là tính năng được nhà cung cấp mô tả, **chưa được xác minh trong app**. `Theo model` không được UI tự diễn giải thành true. Context là token trừ khi ghi khác; effective limit còn bị tier, runtime và output budget giới hạn.

| Provider | API format / OpenAI compatible | Context | Streaming | Structured output | Key / độ phức tạp tích hợp |
|---|---|---|---|---|---|
| NVIDIA NIM | Hosted catalog và NIM tự host là hai môi trường; chat completions tương thích OpenAI [N1][N2] | Theo model/deployment; không có một limit cho NIM | Chat có stream [N1] | Xác minh theo model/endpoint; chưa có căn cứ để bật toàn catalog | Hosted cần NVIDIA key; trung bình do trial/region/model metadata; tự host cao |
| OpenRouter | `/api/v1/chat/completions`, unified OpenAI-style [O1] | Theo model và endpoint; lấy catalog [O2] | Có qua chat API; kiểm adapter | Theo `supported_parameters`, không đồng đều [O2] | Bearer key; trung bình do downstream routing/data policy |
| Gemini | Native generateContent/streamGenerateContent; có OpenAI compatibility [G1] | Ví dụ 2.5 Flash input 1.048.576, output 65.536 [G2] | Có; hiện adapter app chưa stream | Có trên model hỗ trợ [G2] | API key; trung bình vì native safety/usage/thinking khác chat |
| Groq | OpenAI-style `/openai/v1`, có một số field không hỗ trợ [R1] | Ví dụ GPT-OSS 120B 131.072; completion 65.536 [R2] | Có; structured + streaming có hạn chế [R3] | Strict chỉ subset model; tài liệu hiện nói structured không kết hợp stream/tool [R3] | API key; thấp–trung bình nếu có transport chung |
| Cerebras | Chat API `/v1`, OpenAI-compatible [C1] | GPT-OSS 120B: trial 65k / paid 131k; Qwen 3.8 27B: 64k /128k [C2] | Có chat stream; cần contract test theo model | JSON schema được mô tả [C3]; không tự suy diễn tổ hợp capability | API key; trung bình, phải phân tier context |
| Cloudflare Workers AI | Native account endpoint; OpenAI-compatible chat/embedding [F1] | Theo model card, không theo “Workers” chung | Theo model | JSON mode/schema, phụ thuộc model [F2] | Account ID + API token; trung bình; không cần deploy frontend lên Workers |
| Hugging Face Inference Providers | Router chat OpenAI-style; SDK cho task khác [H1] | Theo backend/model; catalog có metadata nếu khả dụng | Theo backend/model | Theo backend/model; phải kiểm tổ hợp | HF token có quyền inference; trung bình vì có router thứ hai |
| Ollama local | `/api/chat` native; một phần OpenAI API [L1] | Limit model khác allocated context; tăng context tăng RAM/VRAM [L2] | Có | Local hỗ trợ schema [L3]; không suy ra cho Ollama Cloud | Local endpoint không cần secret thực; thấp API, trung bình vận hành model |
| LM Studio local | Native `/api/v1/*` và OpenAI `/v1/*` [S1] | Model maximum khác loaded context; native API cho metadata/load | Có [S1] | JSON schema; chất lượng semantic vẫn cần đo [S2] | Có cấu hình API token; loopback tùy cấu hình; thấp–trung bình |
| Alibaba Qwen-MT | Native DashScope hoặc OpenAI-compatible với `translation_options` [Q1] | Input tối đa 8.192 gồm references [Q1] | Flash/lite incremental; plus/turbo không [Q1] | Không mặc định JSON/schema cho MT; output text dịch | DashScope key + region/workspace; trung bình do MT payload riêng |

## 3. Miễn phí, quota, ổn định và thương mại

Độ phức tạp/khuyến nghị là nhận định cho repo này. Không đánh đồng open weights với free inference hoặc free tier với quyền xuất bản bản dịch.

| Provider | Free tier / free models / trial | Rate limit công khai, chưa xác minh account | Stability và latency | Commercial restriction / dữ liệu |
|---|---|---|---|---|
| NVIDIA NIM | Catalog cho thử nghiệm; không gắn nhãn miễn phí vĩnh viễn | Không chốt RPM từ lời truyền miệng; quota theo service/account | CHƯA ĐO; trial/pre-release không được xem là nền tảng SLA | Trial terms giới hạn internal testing/evaluation; production cần subscription thích hợp [N3]. Phân biệt license weights và service. |
| OpenRouter | Các slug `:free` thực sự có trong catalog; không tự thêm suffix cho mọi model | 20 RPM, 50 request/ngày; 1.000/ngày nếu đã mua >= $10 credits theo docs [O3] | CHƯA ĐO; free availability thấp hơn kỳ vọng batch truyện dài; router ngẫu nhiên không đảm bảo cùng model | Điều khoản router + backend + model; phải chọn data policy và disclosure phù hợp. Free không tự chứng minh quyền thương mại. |
| Gemini | 2.5 Flash/Flash-Lite có free tier theo pricing; không suy ra mọi model miễn phí [G3] | RPM/TPM/RPD theo project/tier; xem AI Studio, không theo số key [G4] | CHƯA ĐO; stable/preview/deprecated phải tách | Pricing ghi free tier có thể dùng để cải thiện sản phẩm, paid không [G3]; cần xét nội dung nào được phép gửi. |
| Groq | Có Free Plan theo model công khai | Ví dụ GPT-OSS 120B: 30 RPM, 1.000 RPD, 8k TPM, 200k TPD; giới hạn thực theo org [R4] | CHƯA ĐO; speed quảng cáo không phải end-to-end dịch ở Việt Nam | Theo service terms và license model; chưa rà quyền cho từng candidate. Production/preview phải tách. |
| Cerebras | **Không có free tier tự gia hạn**. Trial $5, 30 ngày, cần verified payment method [C4] | Trial GPT-OSS/Qwen 3.8: 5 RPM, uncached 30k TPM, total 90k TPM, 1M TPD [C4] | CHƯA ĐO; khi hết trial API dừng cho đến mua credits | Có commercial paid path; trial không phải giải pháp free lâu dài. Terms model cụ thể cần xét khi chốt. |
| Cloudflare | 10.000 neurons/ngày; vượt mức cần Workers Paid; không đồng nghĩa 10.000 tokens [F3] | Model/operation limit cộng daily allocation; reset 00:00 UTC [F3] | CHƯA ĐO; neuron cost khác từng model, cần estimate đúng | Điều khoản Cloudflare và license model; không mặc định mọi model có cùng quyền. |
| Hugging Face | Free users $0,10 monthly credits, “subject to change”; hết credits cần mua [H2] | Theo selected backend/account; không có universal RPM phù hợp mọi model | CHƯA ĐO; thêm routing layer; catalog Hub lớn không đồng nghĩa toàn bộ có inference | Model license/gated acceptance và service terms; không lấy “host trên HF” làm chứng nhận license. |
| Ollama local | Inference local không tính tiền token cho API service; vẫn tốn điện, RAM, disk | Theo tài nguyên/runtime; queue local, không có cloud RPD | CHƯA ĐO trên PC này; cold load và tranh RAM với TTS quan trọng | License runtime và weights tách biệt; lựa chọn model có license phù hợp [L4]. Cloud mode không thuộc cam kết local. |
| LM Studio local | App công bố free for use at work [S3]; local inference vẫn tốn tài nguyên | Theo runtime và tài nguyên; không phải quota cloud | CHƯA ĐO; loaded/unloaded model và GUI memory cần tính | App miễn phí dùng tại nơi làm việc không thay license weights [S3]. |
| Qwen-MT | Singapore có quota 1M tokens, hiệu lực 90 ngày theo điều kiện activation/release/approval; region khác không có quota đó [Q2] | Singapore MT: 60 RPM /100k TPM theo docs; các region khác có thể khác [Q3] | CHƯA ĐO; MT chuyên dụng cần đo văn học, không suy từ mô tả “best” | Chọn region/workspace trước account quote; rights nguồn và điều khoản cloud vẫn áp dụng. |

### Chất lượng ngôn ngữ: không thay phép đo bằng tên provider

| Provider | Tiếng Việt | ZH → VI | JA → VI | KO → VI | Truyện dài / consistency |
|---|---|---|---|---|---|
| NVIDIA NIM | CHƯA ĐO / theo model | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO |
| OpenRouter | CHƯA ĐO / theo model thực | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO; ghi downstream model |
| Gemini | CHƯA ĐO | CHƯA ĐO; adapter hiện thiên ZH | CHƯA ĐO; prompt cần đổi | CHƯA ĐO; prompt cần đổi | CHƯA ĐO; context lớn không đảm bảo tên/xưng hô |
| Groq | CHƯA ĐO / theo model | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO |
| Cerebras | CHƯA ĐO / theo model | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO |
| Cloudflare | CHƯA ĐO / theo model | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO |
| Hugging Face | CHƯA ĐO / theo weights + backend | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO |
| Ollama | CHƯA ĐO / theo quantization | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO; effective context local |
| LM Studio | CHƯA ĐO / theo quantization | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO | CHƯA ĐO; cùng weights vẫn khác runtime |
| Qwen-MT | Vietnamese nằm trong supported languages [Q4] | Có coverage; CHƯA ĐO quality | Có coverage; CHƯA ĐO quality | Có coverage; CHƯA ĐO quality | CHƯA ĐO; input limit cần chunk/context selection |

Benchmark kiến thức/code hay tokens/second của vendor không đủ để phong “dịch truyện tốt nhất”. Kế hoạch đánh giá và shortlist nằm ở [model-research.md](model-research.md).

## 4. Options cho phạm vi tích hợp

| Tiêu chí | A — Ít thay đổi | B — Cân bằng, khuyến nghị có điều kiện | C — Catalog rộng |
|---|---|---|---|
| Phạm vi | Sửa hợp đồng Gemini/Qwen, thêm ít lựa chọn curated | Resolve profile thống nhất, Gemini + OpenRouter + một runtime local; Qwen giữ khi contract đúng | Thêm mọi vendor trực tiếp, routing policies đa tầng |
| Complexity / migration | Thấp / thấp | Vừa / vừa | Cao / cao |
| Maintainability | Nhánh provider vẫn tăng | Shared HTTP transport + native adapter mỏng | Nhiều tổ hợp capability/tier cần bảo trì |
| UX | Ít model dễ học, ít lựa chọn | Recommended trước, advanced catalog sau | Dễ quá tải, phải thiết kế filter/eligibility tốt |
| Scalability | Đủ tác vụ đơn, hạn chế fallback | Đủ local single-worker, mở dần theo nhu cầu | Tốt cho nhu cầu rộng nhưng chưa có nhu cầu SaaS |
| Thời gian tương đối | Ngắn nhất | Trung bình; tính estimate sau chốt scope | Dài nhất; không thể coi “base_url khác” là xong |
| Risk | Tiếp tục coupling | Cần migration profile/cache/ledger rõ | Scope creep và hành vi router khó tái hiện |

**Khuyến nghị sơ bộ B**, giữ local-first và backend/domain hiện có. Đây là input cho decision point của master research, chưa là kiến trúc đã duyệt. Thứ tự có thể cân nhắc:

1. **P0 foundation:** sửa các bất nhất PR-R01–04 trong một implementation session sau, chuẩn hóa credential/error/attempt/budget. Chưa thêm vendor trước khi nền tảng đi đúng.
2. **Provider Phase 1 (đề xuất):** Gemini để bảo toàn đường đang có; OpenRouter để kiểm chứng transport chung và lựa chọn free; một local runtime do người dùng chọn Ollama hoặc LM Studio. Không bắt triển khai cả hai runtime.
3. **Provider Phase 2 (đề xuất):** Qwen-MT như candidate chuyên dịch sau xác minh wire contract; Groq như ứng viên latency; NIM chỉ trong phạm vi thử nghiệm phù hợp terms, production cần deployment/subscription khác.
4. **Optional:** Cerebras (trial/paid tốc độ), Cloudflare (nếu account/ecosystem sẵn), HF (model chuyên biệt), runtime local thứ hai. Không có lý do hiện tại để implement đủ 10 provider cùng lúc.

## 5. Capability và fallback: nguyên tắc đề xuất để thảo luận

Transport OpenAI-style dùng chung được cho headers, HTTP lifecycle, SSE framing, error envelope cơ bản. Provider descriptor vẫn phải quyết định authentication, endpoint allowlist, parameter mapping, model discovery, token limits, usage extraction và tổ hợp tính năng. Native MT như Qwen không nên bị ép qua prompt chat làm mất terms/TM. Không bật tools, web search hay code execution cho công việc dịch thuần túy.

Execution plan cần snapshot `profileRevision`, requested model, resolved model nếu biết, input/output caps, prompt/context hashes, credential reference, free-policy và budget ceiling. Không resolve profile một lần ở route rồi lại resolve model độc lập ở workflow. Metadata pricing/account availability phải có nguồn và ngày, không để số thiếu thành zero.

| Tình huống | Xử lý đề xuất | Điều kiện đổi model/provider |
|---|---|---|
| 429 rate limit ngắn | Ưu tiên Retry-After; nếu không có dùng backoff có jitter, deadline/attempt cap | Chỉ fallback trong danh sách đã cho phép, budget và consent phù hợp |
| Quota/credit exhausted | Tạm dừng job, hiển thị reset/credit requirement nếu xác định được | Không tự chuyển free → paid |
| 401/403 | Dừng profile, báo cần kiểm tra credential/access | Không lặp retry vô ích; chỉ route khác đã được cho phép |
| 404/model retired | Refresh metadata; đánh dấu unavailable | Dùng explicit fallback, giữ requested/actual model và lý do |
| 5xx/outage trước xác nhận xử lý | Retry bounded; cân nhắc circuit breaker theo profile sau lỗi liên tiếp | Không hai tầng retry cùng nhân số lần thử |
| Timeout/ngắt stream sau gửi | Lưu trạng thái attempt và usage confidence UNKNOWN | Mặc định không tự dispatch lượt mới có thể tính tiền; partial text không thành approved translation |
| Context overflow | Recompute token budget, chunk theo ranh giới nghĩa; giữ locked terms/source | Chỉ model context lớn hơn nằm trong allowlist; không bỏ source cho vừa |
| Safety refusal | Giữ lỗi có nghĩa, để người dùng review | Không chạy loop provider để né refusal |
| Malformed response | Lưu lỗi schema/finish reason đã redact; retry hữu hạn nếu policy cho phép | Không coi JSON hợp lệ là dịch đủ nội dung |

Circuit breaker không cần Redis hay service mới ở quy mô này. Nếu được duyệt, trạng thái nhẹ theo profile/model, cooldown và một half-open probe đã đủ; persist attempt/job để restart không nhân request. Budget accounting vẫn dựa từng attempt, kể cả khi router thực hiện downstream fallback. Cần đọc provider docs về phần usage mà router thực trả trước khi chọn audit granularity.

## 6. Nguồn chính thức đã đọc

Tất cả truy cập ngày **2026-09-07**. Mã nguồn nội bộ là bằng chứng current behavior; nguồn dưới đây chỉ mô tả dịch vụ, không xác minh account của người dùng.

- [N1 — NVIDIA NIM API reference](https://docs.nvidia.com/nim/large-language-models/latest/api-reference.html)
- [N2 — NVIDIA hosted LLM APIs](https://docs.api.nvidia.com/nim/reference/llm-apis)
- [N3 — NVIDIA API Trial Terms, mục 1.3–1.4](https://assets.ngc.nvidia.com/products/api-catalog/legal/NVIDIA%20API%20Trial%20Terms%20of%20Service.pdf)
- [O1 — OpenRouter quickstart](https://openrouter.ai/docs/quickstart)
- [O2 — OpenRouter model catalog API](https://openrouter.ai/docs/guides/overview/models)
- [O3 — OpenRouter free quota](https://openrouter.ai/blog/tutorials/how-to-get-the-lowest-cost-llm-inference-on-openrouter/)
- [O4 — OpenRouter free router](https://openrouter.ai/docs/cookbook/get-started/free-models-router-playground)
- [G1 — Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
- [G2 — Gemini 2.5 Flash model card](https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash)
- [G3 — Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [G4 — Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [G5 — Gemini lifecycle](https://ai.google.dev/gemini-api/docs/deprecations)
- [R1 — Groq compatibility](https://console.groq.com/docs/openai)
- [R2 — Groq model specifications](https://console.groq.com/docs/models)
- [R3 — Groq structured outputs](https://console.groq.com/docs/structured-outputs)
- [R4 — Groq rate limits](https://console.groq.com/docs/rate-limits)
- [C1 — Cerebras API overview](https://inference-docs.cerebras.ai/)
- [C2 — Cerebras model catalog](https://inference-docs.cerebras.ai/models/overview)
- [C3 — Cerebras structured outputs](https://inference-docs.cerebras.ai/capabilities/structured-outputs)
- [C4 — Cerebras trial and limits](https://inference-docs.cerebras.ai/support/rate-limits)
- [F1 — Workers AI OpenAI compatibility](https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/)
- [F2 — Workers AI JSON mode](https://developers.cloudflare.com/workers-ai/features/json-mode/)
- [F3 — Workers AI pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/)
- [H1 — Hugging Face Inference Providers](https://huggingface.co/docs/inference-providers/index)
- [H2 — Hugging Face pricing](https://huggingface.co/docs/inference-providers/pricing)
- [L1 — Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [L2 — Ollama context allocation](https://docs.ollama.com/context-length)
- [L3 — Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
- [L4 — Qwen3-4B official weights/model license](https://huggingface.co/Qwen/Qwen3-4B)
- [S1 — LM Studio native and compatibility APIs](https://lmstudio.ai/docs/developer/rest)
- [S2 — LM Studio structured output](https://beta.lmstudio.ai/docs/developer/openai-compat/structured-output)
- [S3 — LM Studio free for work](https://lmstudio.ai/blog/free-for-work)
- [Q1 — Alibaba Qwen-MT contract](https://www.alibabacloud.com/help/en/model-studio/machine-translation)
- [Q2 — Alibaba model pricing, Qwen Translation section](https://www.alibabacloud.com/help/en/model-studio/model-pricing)
- [Q3 — Alibaba model rate limits](https://www.alibabacloud.com/help/tc/model-studio/rate-limit)
- [Q4 — Alibaba supported language table](https://www.alibabacloud.com/help/id/model-studio/machine-translation)
